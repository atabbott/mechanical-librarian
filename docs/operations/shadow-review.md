# Qualification-only shadow review

This development surface produces untrusted advice for one exact current
ReviewCase. It cannot decide, approve, apply, write to Audiobookshelf or media,
run in the background, retry automatically, or activate normal review.

Private cohort qualification uses the separate
[qualification-evidence contract](../development/reviewer-qualification.md).
Its append-only experimental retry accounting does not change this command's
product behavior: there is still no automatic retry, and an ordinary retained
timeout is a terminal diagnostic observation for the experiment that observed
it.

## Qualification profile

Create an owner-only (`0600`) canonical JSON file matching
[`reviewer-qualification-profile-v1`](../reference/reviewer-qualification-profile-v1.schema.json).
It declares absolute local paths and exact SHA-256/version identities for a
maintainer-provisioned Codex CLI and Bubblewrap, a private `0700` auth-store
directory, an empty-job parent, and explicit resource limits. Paths and auth
material are never stored in the application database. The tuple is fixed to:

- model `gpt-5.6-sol`, reasoning `medium`;
- prompt `shadow-review-prompt-v3`;
- profile `codex-shadow-profile-v2`;
- packet `shadow-review-packet-v5`;
- request `shadow-review-request-v8`;
- output `shadow-review-result-v2`;
- policy `qualification-only-policy-v1`; and
- sandbox `bubblewrap-v1`.

The runtime never installs or upgrades either artifact. A missing, changed, or
wrong executable is `setup`; an unknown tuple or malformed profile is
`profile`. Codex CLI and Bubblewrap are external qualification prerequisites,
not Python dependencies. Record their licence/provenance with the private
qualification evidence; F15a owns supported bundling, licence inventory, and
SBOM inclusion.

Packet v5 retains raw candidates, citations, correlations, and bibliographic
combinations and adds a closed decision summary. Provider support is the count
of distinct admitted provider correlation groups, not citation count; local
support is the distinct selected-EPUB group. Target-only preservation is
separate and never reported as independent evidence. Work-scoped facts are
associated-work-only, edition facts are `established` only with an exact
selected-EPUB or accepted provider manifestation anchor, and all other edition
bindings are `unresolved`. Complete author candidates name included author
citations and same-representation typed exclusions such as narrator/editor;
other fields have empty role summaries. Publication candidates state exact
date/year pairing and remain report-only with unsupported writes.

The proposal summary is not reviewer advice. `candidate`, `keep_current`, and
`manual` describe only the existing closed ProposalSet/ReviewCase outcome.
Before request creation, the trusted domain compiler independently evaluates
every candidate action, keep-current action, and the explicit manual action
from packet and fixed policy facts. It emits stable action identities, exact
citation and bibliographic-combination closures, and typed exclusions for
rejected nonmanual actions. Unknown obligations fail closed. It does not read
gold decisions, prior reviewer output, scores, latency, titles, human identity,
or response metadata.

Request v8 binds a finite structured-output `anyOf` containing only those
compiled actions. Review result v2 still makes the untrusted recommendation;
trusted validation requires the exact compiled action, action-bound citations,
complete proposal coverage, one coherent bibliographic combination where
applicable, and currentness.

Candidate advice names a candidate only for `recommend_candidate`; manual and
keep-current use JSON `null`. Keep-current is a positive cited conclusion that
the current value is supported under the closed destination policy, not a
fallback. Insufficient, conflicting, explicitly manual, or unrepresentable
evidence remains manual. `problems` is always present but contains only actual
adverse facts, so it may be empty when the bounded reason and exact citations
support manual review without an adverse fact.

## Authentication

All auth and review processes serialize on one owner-only auth-store lock:

```sh
mechanical-librarian reviewer-auth-start --qualification-profile profile.json
mechanical-librarian reviewer-auth-status --qualification-profile profile.json
mechanical-librarian reviewer-auth-logout --qualification-profile profile.json
```

Start uses device authentication. Status accepts only the exact canonical
ChatGPT subscription line on either process stream when the command succeeds
and the other stream is empty. Mixed, additional, malformed, API-key, and
nonzero results are rejected. Logout is explicit and never performed by
preview, request, inspect, or reevaluate.

## Preview and request

Preview validates only profile bytes and the exact persisted case lineage. It
does not inspect declared executables or auth state, construct a target,
provider, reviewer, or other network client, or invoke Codex:

```sh
mechanical-librarian shadow-review-preview --database reports.sqlite \
  --review-case-id review_case_<64-lowercase-hex> \
  --qualification-profile profile.json
```

Preview is not fresh eligibility proof. Request atomically revalidates the
current persisted case/report/proposal/profile/packet, persists the request and
attempt, then performs at most one foreground invocation:

Provider date citations in the sanitized packet carry the exact immutable
report `accessed_at` value alongside authority and query kind, or explicit
`null` when the report records that no provider dispatch occurred. Preview,
request, restart, and reevaluation never access a provider or refresh the
stored time. They also carry at most 16 bounded sibling ISBN/ASIN fact objects
and 16 physical-format facts. Identifier references must match those fact
objects exactly after the runtime reconstructs each fixed valid sibling claim
from its public-safe provider/candidate context, kind, value, applicability,
edition scope, and raw order. ISBN checksums and closed uppercase ASIN syntax
are mandatory. Missing linkage on either a typed provider date claim or rich
date evidence, opaque-only references, content-address mismatch, private or
credential-shaped context, malformed/noncanonical facts, or over-bound
linkage rejects the packet before invocation. Candidate date typing describes
the target destination (`manifestation_publication` plus `year`, `month`, or
`day`);
source event and precision remain in the citation. This exact packet/request
freeze is governed by ADR-0016. Provider-linked packet/request replay is not a
standalone operation: persistence reopens the immutable source report,
reevaluates its proposal set and review case, rebuilds the complete packet, and
requires exact equality before dispatch or inspection. Every ordinary JSON
replay requires that authoritative report; validation does not infer
provenance requirements from mutable packet fields. Missing report context,
rewritten destinations or date typing, changed proposal content, and invented
or omitted citations or identifier/format siblings fail closed even when all
serialized identities are preserved. The report itself never enters the
reviewer request.
Every projected candidate evidence reference must name an included citation.
Deleting a provider citation, its linked local citation, or both therefore
cannot turn a source-bound packet into a source-free packet or leave dangling
proposal evidence.
Every non-null local claim reference used as an operand by a retained provider
date link is also included as a source-bound citation. Removing or changing
that local citation makes packet replay fail; an intentionally unbound
manifestation-incompatible link has no local citation.

The packet also includes `acceptable_choice` language and publisher proposals.
Those proposals retain every distinct admitted candidate value, assessment,
correlation, and evidence reference and carry no preselected candidate.
Compatible language codes are not rewritten to one spelling, and plausible
publisher/imprint/brand/group statements are not represented as a corporate
equality or hierarchy. A result may recommend an included candidate under the
ordinary untrusted-advice contract; the packet itself grants no decision or
write authority.

Packet v5 also carries the exact `bibliographic-relationships-v1`
combinations from the ProposalSet and includes every constrained title,
subtitle, and series proposal, including a non-actionable preserve companion
with no candidates. Result v2 still contains only the per-destination
recommendations and has no model-controlled bibliographic-combination field.
Semantic validation derives their exact
candidate-or-preserve member tuple and requires it to match exactly one allowed
combination. A candidate from another tuple, a manual recommendation inside a
selected tuple, or contradictory keep/change advice rejects the whole result.
The matched combination ID is an audit derivation from the exact request and
result; it is not hidden in prose and is not another model-controlled field.
When conservative duplicate detection cannot represent a safe tuple, the
packet exposes all title/subtitle/series members as manual. Members with
retained candidates remain unresolved raw results; exact target-bound
companions carry no invented candidate and become manual only in the review
projection. No prompt, output schema, or decision authority is added.

```sh
mechanical-librarian shadow-review-request --database reports.sqlite \
  --review-case-id review_case_<64-lowercase-hex> \
  --qualification-profile profile.json
```

An identical request command reopens state and never egresses again. Inspect is
offline:

```sh
mechanical-librarian shadow-review-inspect --database reports.sqlite \
  --review-request-id review_request_<64-lowercase-hex> \
  --qualification-profile profile.json
```

A successful status is permanently `qualification_only`. A dispatched
timeout, cancellation, runtime, response-format rejection, malformed output, or
semantic-validation failure remains `unknown`, because
the external service may have received the packet. There is no silent retry.
After the supervised Linux process tree is proved absent, an operator may make
one explicit new attempt:

```sh
mechanical-librarian shadow-review-reevaluate --database reports.sqlite \
  --review-request-id review_request_<64-lowercase-hex> \
  --qualification-profile profile.json
```

The reevaluation claim compares the exact latest attempt inside the write
transaction, so concurrent operators cannot both append or overlap. If a
process ended after durable dispatch but before a terminal outcome was stored,
inspection reports `unknown`. If the application stopped before dispatch, the
attempt remains `pending`; explicit reevaluation first proves the exact
persisted claimant PID and Linux start token absent, then may safely claim its
successor. Any unreleased controller could not execute or egress.
The transaction permits reevaluation only when the latest attempt has no
outcome, or has a failure after durable dispatch. A successful result and a
known pre-dispatch failure are terminal; the command rejects them without
starting another reviewer.

Executable paths are opened, verified, and held by descriptor through version,
auth, and sandbox invocation. Authentication runs through the same pinned
Bubblewrap boundary as review. Explicit `reviewer-auth-start`, status, and
logout operations retain the writable private authentication store. Ordinary
review never mounts that store writable: it creates a private `provider-state`
directory inside the attempt job, mounts that directory at `/auth`, and nests
only the primary `auth.json` file as a read-only `/auth/auth.json` mount through
its validated, held no-follow descriptor. The
ordinary preflight status check and inference use that same contained state.
The version probe runs inside Bubblewrap with an empty, credential-free home
and an unshared network namespace.
In both workflows `/auth/config.toml` is an immutable empty regular file from Bubblewrap's
`--perms 0400 --ro-bind-data` bind for the whole child lifetime. This remains
effective if the same UID creates or replaces the underlying file after
preflight. Auth therefore uses only supported
`login --device-auth`, `login status`, and `logout` command scopes plus exact
ChatGPT/file-store/update/telemetry constraints; it never relies on the
execution-only `--ignore-user-config` option.

Provider cache, database, log, and runtime files therefore remain inside one
disposable attempt. The runner fingerprints the owner-only primary credential
before execution and after terminal cleanup without retaining its bytes or
digest. Credential drift is preserved, writes a value-free owner-only
`.reviewer-trust-failure` marker, and blocks later dispatch pending explicit
recovery. A retained attempt directory or the same durable marker from failed
cleanup also blocks later dispatch, even if removal completed before returning
an error; cleanup failure is a terminal runtime failure, not best-effort housekeeping.

During maintainer qualification, invoke the wrapped real-artifact compatibility
check with the already provisioned exact Python environment. Provision the pinned
Codex CLI and Bubblewrap tuple before freezing the profile; the check never
installs, syncs, or upgrades runtime artifacts. It suppresses raw process
streams and emits only aggregate value-free evidence. Its first stage runs
exactly three help shapes with no credential operation, network request,
prompt, packet, or model invocation. That stage adds a probe-only Bubblewrap
network namespace and derives a temporary empty auth store under the private
job root, so it neither opens nor mounts live credentials. Its second stage
uses the owner subscription for exactly one live synthetic request. Normal
device auth retains its network access.

```sh
python scripts/qualify_reviewer_cli_parser.py \
  --qualification-profile /absolute/private/profile.json
```

Process-local failures retain the adapter's narrow internal signals, but every
durable and CLI boundary maps them to the ADR-0017 diagnostic object. It
contains only category, closed component/stage, optional
retryability/currentness, and bounded count. Raw stdout, stderr, reasoning,
malformed content, profile paths, auth data, exception text, and digests are
discarded from those projections.
The pinned ordinary command runs with JSONL events enabled. Exit status 1 maps
to `live_schema_rejection` only when stderr is empty and its bounded,
strict-UTF-8 JSONL stream ends in the exact `turn.failed` shape whose sole
message begins with the pinned service rejection for
`response_format 'codex_output_schema'`. The message and the rest of both
streams are discarded. A different exit status, over-bound or malformed
stream, additional terminal fields, stderr output, or any other error message
is conservatively `spawn_runtime`. Malformed result documents and zero-exit
semantic or citation failures remain their own diagnostic categories.

Offline parser-help probes remain no-network and use a temporary empty auth
store. The one-case compatibility canary is a real synthetic,
subscription-backed ordinary invocation before a larger qualification cohort;
it creates a complete deterministic production packet/request/schema, uses the
same dispatch-before-release execution gate, resource limits, timeout, output
parser, semantic validator, and cleanup as an ordinary request, and therefore
uses the exact ordinary wrapped command from empty `/job`, including
`--skip-git-repo-check`. It never sends a holdout and blocks the cohort on
failure. Each request binds the canonical structured-output schema bytes and
SHA-256 into `shadow-review-request-v8` and its identity; the executor uses
those exact bytes. The complete canonical request envelope is bounded at
3,227,648 bytes, and the exact-current SQLite column has the same byte capacity;
there is no truncation. The generated schema is restricted to the pinned
structured-output subset: nested action variants use `anyOf`, never `oneOf`;
every `const` and `enum` has its primitive type, and
citation cardinality remains bounded without `uniqueItems` so later duplicate
normalization can remain compatible.
Invalid UTF-8, JSON, duplicate keys, noncanonical JSON, and truncated documents
are durable `malformed_output` diagnostics. After canonical outer shape and
exact request-ID syntax are established, a result that names a different exact
request is `identity` at the reviewer identity stage, before citation
normalization or membership checks. A missing or malformed request identity
remains a semantic failure. Other canonical results that violate their bound
request remain `citation_membership`; none retains raw bytes, rejected values,
request IDs, or exception digests in status. Exact repository failures map to
`persistence` with the persistence component and stage. A store failure can
prevent a new durable outcome, but its CLI projection remains the same bounded
diagnostic.
For a canonical document, the adapter validates every raw citation identifier
against the exact packet allowlist and enforces the 256-item raw bound before
normalizing each recommendation to a sorted unique set. Reordering and known
duplicates alone therefore do not invalidate advice. Unknown, missing,
malformed, mixed-validity, or over-bound citations still fail semantic
validation; normalization never adds evidence or repairs recommendation
coverage, candidate membership, or currentness.

The raw result bytes remain unchanged in the private bounded job path until its
existing cleanup. Failures classify those exact private bytes before cleanup.
Successful normalization
crosses the adapter seam only as an in-memory typed marker containing a changed
flag and aggregate duplicate count; raw identifiers and ordering are not
exposed, and the marker does not enter canonical result JSON, persistence,
identity, or replay.

`shadow-review-inspect-v3` is an offline aggregate status projection. It emits
the case and request identities, currentness, final status, bounded total
attempt count, sorted unique canonical diagnostic aggregates, and one bounded
execution-provenance projection per terminal attempt. Provenance separates the
requested model, effort, profile and pinned invocation identity from provider
observations of model, route, tier, effort, fallback, usage, and cost. Each
observation is `observed`, `not_exposed`, or `unverifiable`; requested values
are never relabelled as observed. The case
identity binds `shadow-review-request` output to its requested case. Duplicate,
split, or out-of-order aggregates fail closed. The projection emits no request,
packet, result, attempt ID/number, PID/process-group/start token, raw output,
credential, private path, raw provider state, or timing.
A dispatched attempt without a durable terminal outcome reopens as `unknown`
with one bounded reviewer/unknown/execution diagnostic; `unknown` and `failed`
inspection states therefore never carry an empty diagnostic list.
`shadow-review-failure-v2` uses the identical diagnostic shape on CLI stderr.
Failure record, durable replay, application inspection, and CLI validation all
enforce the same closed reviewer diagnostic shapes. Impossible relabellings,
such as a provider HTTP response with a reviewer component, fail closed without
retaining the rejected value.
The production-signal mapping matrix covers every frozen diagnostic category;
unknown producer signals fail closed to `unknown` rather than borrowing a
nearby category.
