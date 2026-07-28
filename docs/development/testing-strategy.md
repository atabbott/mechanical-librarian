# Testing strategy

The public gate checks the lockfile and runs the canonical exact-artifact
qualifier:

```bash
python3 scripts/qualify_exact_artifact.py \
  --source-sha "$(git rev-parse HEAD)"
```

That procedure requires a clean checkout at the supplied source SHA, snapshots
the committed source, disables the `uv` build and install cache, validates the
wheel's bounded archive and `RECORD` before installation, seeds a new
environment from all locked dependency groups, and installs the explicit wheel
pathname without dependency resolution. It proves the wheel and installed
distribution payloads are byte-identical, validates the installer-updated
`RECORD`, rechecks the whole-wheel hash, and runs the entire public suite with
the isolated environment's interpreter. It then checks the PEP 610 install
origin and invokes the installed console command from outside the checkout.
Its path-free JSON evidence binds the source SHA, wheel SHA-256, payload
SHA-256, and console-output SHA-256. A dirty or different source, ambiguous or
changed wheel, invalid archive/record, reused destination, unlocked dependency,
origin mismatch, payload mismatch, stale test install, or non-installed
import/command fails closed. The installed console proof performs exact-ID
report and ProposalSet creation, concurrent admission, replay, restart-backed
get, successor/historical projection, and nonactionable retirement against a
disposable loopback target and synthetic EPUB.

The suite covers:

- closed reports, canonical serialization, stable claim/report references,
  title normalization, and occurrence-local ISBN applicability, including
  explicit non-ISBN scheme and URI controls across package, opening, and rear
  evidence; controlled contributor-role separation, compound roles, contextual
  production-credit negatives, and typed manifestation/work/copyright/
  creation/modification/conversion date events at exact year/month/day/signed-
  timestamp precision across package, front, and rear evidence;
- strict configuration, duplicate/reference rejection, and no implicit inputs;
- scoped Audiobookshelf item reading, response bounds, root mounts,
  `relPath`/`path` agreement, all 16 destination states/currentness, multiple
  files, and opaque identity across reorder;
- isolated EPUB traversal, expansion limits, malformed markup, external/entity
  exclusion, timeout, child failure, invalid IPC, and output bounds;
- provider discovery-plan construction, disabled/no-input zero egress,
  exact per-lane and aggregate request cardinality, typed no-subset overflow,
  collision-free lane candidate references, sparse candidates, every valid
  ISBN family plus the optional complete-author work lane, fail-closed work
  qualification, typed taxonomy/publication scope, all-or-nothing malformed
  handling, and identity order. Hardcover coverage additionally fixes both
  operations at three relationship levels, proves the bounded sibling taxonomy
  join, inspects valid error-only and partial-data GraphQL errors before data,
  rejects malformed/UTF-8/empty/over-bound error envelopes, and preserves
  timeout, HTTP-status, byte, lane-order, and raw-token confinement behavior.
  Fixed-policy retry coverage proves the 27-attempt observation ceiling,
  500/502/503/504 and narrow typed-transport allowlists, rejection of unsafe
  transport causes, 503-only and rate-limit `Retry-After` semantics, shared
  one-second pacing, cumulative backoff, deadline gates, cancellation, immediate
  partial-body charging, and aggregate-safe diagnostics;
- Open Library exact per-family Search admission, canonical edition/work
  request order, shared one-second pacing, finite rich evidence, terminal
  failure mapping, identified metadata/cover requests, cover-failure isolation,
  provider-order/contact privacy, one retry per request, exactly four retries
  and 49 attempts per observation, terminal 429, 503-only `Retry-After`,
  pacing/backoff/deadline/byte/cancellation bounds, and the explicit five-item
  batch envelope;
- Google Books keyed and credential-free fixed-origin requests, disabled
  zero-egress, exact ISBN/work qualification, unresolved work linkage,
  evidence-only publication strings, deterministic replay and provider order,
  response/candidate/request/aggregate bounds including six attempts per query
  and 54 per observation, quota/4xx/503/no-match typing, canonical
  `Retry-After`, accepted/rejected transport classes, deadline and partial-byte
  bounds,
  field-local scalar failures, strict identifier-entry shapes,
  cancellation/timeout behavior, and credential/raw-response redaction;
- strict `batch-manifest-v5` replay and clean-current rejection, with exact
  54/27/49 Google Books/Hardcover/Open Library metadata-attempt maxima, one
  shared pacer per paced adapter across foreground entries, and no persisted
  attempt diagnostics;
- cover-gallery provider/aggregate bounds, safe provenance, timeout/rejection
  states, visible truncation, and digest deduplication with all provenance;
- exact clean-state DELETE-mode report/ProposalSet/ReviewCase schema, side-effect-free existing-file
  validation, no-follow connection binding, Linux owner-directory lock and
  competing-process serialization, root-to-parent chain validation, verified
  private-alias binding, raced
  configured-sidecar isolation, final-precommit parent replacement rollback,
  alias cleanup, append-only history, replay, scope-checked successor
  pointers, restart readback, corrupt-row rejection, and rollback;
- fixed-policy exact 16-result enumeration, complete author/narrator lists and
  package-creator placeholder exclusion without evidence mutation, explicit
  anonymous/literal-name and context controls, atomic series, independent
  corroboration, accepted-versus-unresolved ISBN applicability, year-only/date
  consistency, populated subjective-field closure, complete cover-gallery
  selection, deterministic ProposalSet replay, stale-report no-insert,
  transaction-local report/current-pointer recheck, exact evaluator
  recomputation before review admission, synthetic ProposalSet rejection, and
  zero provider calls during proposal creation;
- rejection before unauthorized parser/provider/publication activity,
  transaction-bound currentness drift, exact-byte snapshot binding, title
  privacy rejection, redaction, and absence of product/media writes;
- reviewer-boundary citation normalization across reordered and duplicate
  known identifiers, exact raw cardinality and allowlist rejection, mixed and
  cross-case failures, aggregate-only markers, canonical result identity, and
  persistence replay; and
- installed package shape, distribution exclusions, generated-schema drift,
  static dependency direction, and retired-surface absence.

The exact-artifact suite includes the activation demonstration and exercises the
CLI against a disposable loopback target, synthetic media, private secrets, and
an absent database. Unit proposal-policy coverage includes whitespace, case, and
width variants of the controlled package creator placeholder. The installed
proposal path uses a whitespace/case variant and requires a nonmutating authors
result under `fixed-conservative-v2`. Persistence tests reject both a
`fixed-conservative-v1` document and the prior proposal-store table shape
without mutation, then prove exact deterministic replay under the current
policy. Every synthetic EPUB member has a fixed timestamp so the probe identity
does not depend on the qualification wall clock. No live library or provider is
used in development or CI.

The #374 synthetic indicia aggregate reports contributor-role and
manifestation-date recall separately from explicit-negative precision. Its
rows are generated fixtures only; qualification output contains aggregate
counts/rates and no EPUB text, private corpus rows, paths, or credentials.
Adversarial regressions additionally cover unsigned date-time prefix
truncation, mixed work-first/selected-edition wording across front, rear, and
installed-report paths in both cue orders and case/punctuation variants,
same-statement versus unrelated-sentence bounds, adjacent independent credits
versus initials and same-person compound roles, and event-aware local-claim
scope replay.

Hostile-input tests name a supported external actor and reachable boundary.
Typed in-process collaborators are not attacker-controlled unless the product
explicitly introduces an untrusted extension seam.
