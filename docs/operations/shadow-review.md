# Qualification-only shadow review

This development surface produces untrusted advice for one exact current
ReviewCase. It cannot decide, approve, apply, write to Audiobookshelf or media,
run in the background, retry automatically, or activate normal review.

## Qualification profile

Create an owner-only (`0600`) canonical JSON file matching
[`reviewer-qualification-profile-v1`](../reference/reviewer-qualification-profile-v1.schema.json).
It declares absolute local paths and exact SHA-256/version identities for a
maintainer-provisioned Codex CLI and Bubblewrap, a private `0700` auth-store
directory, an empty-job parent, and explicit resource limits. Paths and auth
material are never stored in the application database. The tuple is fixed to:

- model `gpt-5.6-sol`, reasoning `medium`;
- prompt `shadow-review-prompt-v2`;
- profile `codex-shadow-profile-v1`;
- packet `shadow-review-packet-v2`;
- request `shadow-review-request-v3`;
- output `shadow-review-result-v1`;
- policy `qualification-only-policy-v1`; and
- sandbox `bubblewrap-v1`.

The runtime never installs or upgrades either artifact. A missing, changed, or
wrong executable is `setup`; an unknown tuple or malformed profile is
`profile`. Codex CLI and Bubblewrap are external qualification prerequisites,
not Python dependencies. Record their licence/provenance with the private
qualification evidence; F15a owns supported bundling, licence inventory, and
SBOM inclusion.

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
the target destination (`manifestation_publication` plus `year` or `day`);
source event and precision remain in the citation. This exact packet/request
freeze is governed by ADR-0016.

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
Bubblewrap boundary as review. Its private auth store is mounted at `/auth`,
but `/auth/config.toml` is an immutable empty regular file from Bubblewrap's
`--perms 0400 --ro-bind-data` bind for the whole child lifetime. This remains
effective if the same UID creates or replaces the underlying file after
preflight. Auth therefore uses only supported
`login --device-auth`, `login status`, and `logout` command scopes plus exact
ChatGPT/file-store/update/telemetry constraints; it never relies on the
execution-only `--ignore-user-config` option.

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

Failures are the value-free codes `runtime`, `setup`, `auth`, `profile`,
`timeout`, `cancel`, `schema`, `response_format`, `malformed_output`,
`semantic_validation`, or `currentness`. Raw stdout, stderr, reasoning,
malformed content, profile paths, and auth data are discarded.
The pinned CLI currently exposes no stable bounded diagnostic that specifically
proves a live response-format rejection. Every nonzero ordinary-command exit,
including exec failure, signal termination, resource/network/service failure,
and an unknown status, is therefore durable `runtime` with only bounded byte
count and digest diagnostics. `response_format` remains reserved until a pinned
canonical signal can support that narrower causal claim.

Offline parser-help probes remain no-network and use a temporary empty auth
store. The one-case compatibility canary is a real synthetic,
subscription-backed ordinary invocation before a larger qualification cohort;
it creates a complete deterministic production packet/request/schema, uses the
same dispatch-before-release execution gate, resource limits, timeout, output
parser, semantic validator, and cleanup as an ordinary request, and therefore
uses the exact ordinary wrapped command from empty `/job`, including
`--skip-git-repo-check`. It never sends a holdout and blocks the cohort on
failure. Each request binds the canonical structured-output schema bytes and
SHA-256 into `shadow-review-request-v3` and its identity; the executor uses
those exact bytes. The complete canonical request envelope is bounded at
3,227,648 bytes, and the exact-current SQLite column has the same byte capacity;
there is no truncation. The generated schema is restricted to the pinned
structured-output subset: every `const` and `enum` has its primitive type, and
citation cardinality remains bounded without `uniqueItems` so later duplicate
normalization can remain compatible.
Invalid UTF-8, JSON, duplicate keys, noncanonical JSON, and truncated documents
are durable `malformed_output` failures. A canonical parsed result that violates
its bound request is instead durable `semantic_validation`; both retain only
bounded byte count and digest diagnostics.
For a canonical document, the adapter validates every raw citation identifier
against the exact packet allowlist and enforces the 256-item raw bound before
normalizing each recommendation to a sorted unique set. Reordering and known
duplicates alone therefore do not invalidate advice. Unknown, missing,
malformed, mixed-validity, or over-bound citations still fail semantic
validation; normalization never adds evidence or repairs recommendation
coverage, candidate membership, or currentness.

The raw result bytes remain unchanged in the private bounded job path until its
existing cleanup. Failures digest those exact bytes. Successful normalization
crosses the adapter seam only as an in-memory typed marker containing a changed
flag and aggregate duplicate count; raw identifiers and ordering are not
exposed, and the marker does not enter canonical result JSON, persistence,
identity, or replay.
