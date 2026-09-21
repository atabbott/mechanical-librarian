# Public CI and Exact-Head Merge Boundary

## Purpose

The tracked repository supplies a reproducible public CI gate. It does not
supply, authenticate, or authorize the exact-head merge controller. This
separation prevents pull-request content from becoming part of the authority
that decides whether that same content may be merged.

The authoritative merge controller, its validator programs, its workflow
definitions, its evidence schemas, and its terminal `main` containment control
are maintainer-owned local controls under ignored `.codex/**`. They are never
tracked in the candidate repository. Maintainers approve and pin those control
bytes independently of candidate content; a file supplied or modified by a
candidate is not trusted merely because it has a familiar name or path.

## Tracked public gate

`.github/workflows/public-gate.yml` is the authority for what the gate runs and
[what merging proves](../../CONTRIBUTING.md#what-merging-proves) reconciles that
workflow against every documented check. What matters at this boundary is
narrower.

The workflow runs when a pull request is opened, reopened, or receives a new
head, and again on the resulting push to `main`. It does not run for a
ready-for-review state change, so delivery publishes one converged draft
candidate wherever possible and requests review explicitly after the exact-head
jobs pass; the post-merge run verifies the merged commit and is not a merge
precondition.

It has read-only repository contents permission and checks out
`github.event.pull_request.head.sha || github.sha` without persisting
credentials — the exact pull-request head SHA rather than GitHub's synthetic
merge revision, and the merged commit on a push. Its concurrency key is scoped
to the pull request on a pull-request event and to the merged commit on a push,
so a new head cancels obsolete work for that pull request while a merged
commit's verification is never cancelled.

The independent qualification jobs remain separate because the earlier serial
workflow exhausted its own 825-second subprocess deadline on four consecutive
candidates, including after internal sharding. No aggregate runner is
maintained: the local controller requires a passing check run for each of the
gate's required jobs, on the exact candidate SHA. The separate container job
builds the shipped image at that same revision and never runs, loads, or pushes
it.

Public CI contains no service credential, private corpus, merge permission, or
branch-protection override. Private qualification remains local and may expose
only the redacted, aggregate exact-commit evidence permitted by its governing
contract.

## Local merge authority

The local-only controller is the final mutation boundary. Before it can merge,
it must independently bind the repository, pull request, base ref and commit,
exact reviewed head, live required checks, resolved review conversations,
linked issue, and qualified autonomous local and connector review evidence.
GitHub approving reviews are deliberately not part of this gate. Immediately
before mutation it must repeat all race-sensitive gates and use GitHub's
expected-head merge guard. It must not offer an administrator, bypass, force,
or protection-weakening path.

After mutation, the local terminal control must prove that the returned merge
commit has the qualified base as its sole parent, has exactly the qualified
head's tree, and is contained by remote `main`. This proves the delivered tree
is the tree already qualified by all three pull-request jobs without spending a
second full Actions run on an equivalent squash-merge tree.
Ambiguous responses and post-mutation failures remain explicit recovery states;
they are not reported as success and are not automatically rolled back.

Candidate code is evidence to inspect, never merge authority. In particular:

- the candidate does not choose controller or validator paths;
- the candidate cannot approve new control digests or authenticate copied
  controls;
- tracked scripts and workflow files are not imported as trusted validators;
- local review artifacts and private qualification records remain ignored and
  bounded; and
- the merge credential is available only to the maintainer-owned control
  environment.

## Renovate platform auto-merge exception

Genuine `renovate[bot]` pull requests for managed version updates explicitly
enabled by `.github/renovate.json` may use GitHub-native pull-request auto-merge
instead of the local controller and connector review; which classes those are is
stated in [dependencies](dependencies.md). The externally configured
default-branch ruleset remains the authority: it requires a pull request, a head
current with `main`, and a successful GitHub Actions check run on that head for
each of the five jobs: `fast-source`, `exact-installed`, `protected-browser`,
`container-image`, and `dependency-audit`. Behind-main branches refresh and
repeat qualification, including outside the new-branch schedule. Renovate has no
bypass actor and cannot merge a branch directly.

This exception does not transfer through a title, label, branch name, commit
author, or human account. Every non-eligible Renovate update and every other
material pull request continues to use the maintainer-owned exact-head merge
controller. Repository configuration changes to the exception itself are
ordinary tracked, reviewed changes and cannot authorize their own merge.

## Controller activation and residual risk

The controller retains the receipt-bound `CLIENT_ENFORCED_FREE_PLAN` validation
mode identifier for evidence compatibility. On GitHub Pro, the active ruleset
independently enforces pull requests and strict required checks. The controller
still records GitHub protection and ruleset responses as canonical, body-free
observations rather than treating them as client-side merge authority. Every
successful controller and train receipt records the fixed residual risk
`EXTERNAL_ACTOR_MUTATION_NOT_PREVENTED`.

The accepted limitation is precise: another qualified pull request or an
administrator changing enforcement can move or race `main`, and GitHub's
conditional merge locks the pull-request head but not the base SHA. The
controller mitigates rather than eliminates that risk. It requires a
controller-only pull request, canonical repository and PR,
the exact bounded head repository and delivery branch, exact reviewed head and
tree, passing current-head checks, resolved review
threads, terminal connector evidence, live base/head/mergeability, and an
internally persisted canonical dry-run receipt followed by a repeated live
preflight and immediate conditional `sha=<expected-head>` squash merge. The
terminal receipt binds that dry-run by digest and exact request, evidence,
controller, controls, and a trusted mutation-attempt timestamp captured beside
the merge call. The 300-second freshness window ends at mutation, not terminal
completion, so longer post-merge verification and reconciliation remain valid;
outcome-specific schemas reject extra receipt fields.
The controller also starts a trusted monotonic 300-second deadline before the
internal dry-run wall timestamp is assigned or the receipt is persisted, so
timestamping, persistence, and fsync time count. Immediately after the repeated
live preflight it captures the mutation-attempt timestamp; then
that deadline guard is the only controller-local operation before the merge
call; expiry produces a typed no-mutation failure.
It then verifies the returned merge has the qualified base as its sole parent,
has the qualified head's exact tree, and is contained by authoritative `main`;
requires the complete native closing set to exactly match the bounded expected
set; reconciles tracking; and deletes
the delivery branch with an exact lease. A base race therefore becomes a typed
post-merge recovery failure, never implicit success.
The complete closing-set proof is capped by its GraphQL query: activation may
bind 99 constituents plus the tracker, while non-activation may bind 100.
If the merge response is ambiguous, the controller performs bounded
post-attempt connector and server observation. The unknown-state receipt binds
exact observed digests or typed unavailable fallbacks and cannot qualify as
`MERGED`; carried recovery observations remain distinct and chronological.
Each carried observation must be at or after the trusted mutation-attempt time,
and connector `FAIL` must prove actual drift rather than a changed timestamp or
failure label alone.
Timing-invalid connector evidence, including boolean, nonnumeric, negative, or
non-finite elapsed time, becomes `UNAVAILABLE` with no digest.
Integer magnitude is bounded by the maximum supported timestamp delta before
conversion in normal and recovery validation, so arbitrarily large JSON
integers follow the same fallback and the exact maximum remains valid. Raw
positive or negative JSON integer tokens longer than 4,300 digits are rejected
at the bounded parser and become typed `UNAVAILABLE` evidence with a null
digest rather than escaping as conversion errors.
The run-record validator routes authoritative GitHub responses, trusted
evidence, and top-level record input through one bounded parser. JSON decode,
integer-conversion, Unicode, and recursion failures become context-specific
record errors; trusted size, canonical-byte, and digest checks remain intact.
The pre-decode lexical scan is JSON-string-and-escape-aware and caps structural
depth at 100 independent of Python decoder behavior. Authoritative REST and
GraphQL stdout and stderr are captured as bytes, ensuring invalid UTF-8 reaches
the parser boundary while raw stderr remains undisclosed.
Byte inputs are strictly decoded as UTF-8 once, and that same immutable string
feeds both the depth scan and JSON decoder. UTF-16 and UTF-32 auto-detection is
therefore unavailable and cannot desynchronize escape or delimiter handling.
The terminal branch-cleanup query byte-captures and canonical-strict-decodes
stdout and stderr through the same UTF-8 boundary. Malformed or alternate
encodings, NULs, and BOMs cannot become a presence/absence result; only a
strictly decoded nonzero HTTP 404 diagnostic proves absence. It must full-match
the canonical single-line GitHub form with empty stdout, a nonempty trimmed
message, and zero or one terminal newline. The message's `splitlines()` result
must contain exactly that message; VT, FF, FS, GS, RS, NEL, Unicode
line/paragraph separators, and other noncanonical whitespace, control, or
format characters are invalid. Mixed, prefixed, multiline, repeated, or
trailing diagnostics cannot prove cleanup.
Protocol variances may prove ambiguous post-attempt connector `FAIL`, but remain
invalid in passing, merged, and normal qualifying connector evidence.

The maintainer-owned controller, validators, evidence contracts, and terminal
containment under ignored `.codex/**` are qualified independently of candidate
content. `Deliver slice <ID>` grants standing authority for the complete
bounded transaction, including exact-head merge and cleanup. Passing public CI
alone is not merge authorization, and no admin, bypass, force, direct-`main`,
release, credential, or production-write path is introduced.
