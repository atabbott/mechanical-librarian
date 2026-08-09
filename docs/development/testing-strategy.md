# Testing strategy

Audiobookshelf write qualification uses synthetic disposable libraries and API
keys only. It proves one metadata/tags PATCH and one digest-verified multipart
cover upload at v2.26.0 and the then-latest stable image, including read-back,
refusal and ambiguous-response reconciliation. It never contacts a production
library or uses direct database or browser writes.

Run the canary from a clean exact head. It archives that head before importing
or running the adapter, creates scoped disposable API-key users, deletes those
keys during teardown where the service remains available, and removes only its
labelled container. Its aggregate receipt contains image digests and outcomes,
never credentials, user tokens, passwords, or image bytes:

```bash
uv run python scripts/qualify_audiobookshelf_field_effect.py \
  --source-sha "$(git rev-parse HEAD)"
```

The public suite has four explicit qualification tiers. They retain every
existing assertion while keeping independent feedback paths parallel and
diagnosable.

F19a adds focused source proof for clean-schema automatic bootstrap, exact
qualified option/bundle admission, atomic human/automatic uniqueness,
restart/idempotency/currentness, pause/suspension/recovery, and the selected
responsive policy notebook. The private semantic corpus remains private; the
public manifest is aggregate-only. The complete exact-artifact tier is the
authoritative cumulative 1,000-work/no-write demonstration.

The fast routine source tier owns unit, focused integration, and static
architecture behavior:

```bash
uv run python scripts/run_test_tier.py fast
```

The required exact-installed-artifact tier owns wheel identity, archive and
installed `RECORD` integrity, installed imports, generated package/schema
integrity, the installed console, and a representative end-to-end lifecycle:

```bash
python3 scripts/qualify_exact_artifact.py \
  --source-sha "$(git rev-parse HEAD)" \
  --test-tier required
```

The protected browser tier remains independently runnable. Install and verify
the repository-pinned Chromium payload before accepting its result:

```bash
uv run playwright install --with-deps chromium
uv run python scripts/verify_playwright_browser.py
uv run python scripts/run_test_tier.py browser
```

The explicit slow tier owns scale, memory/resource ceilings, endurance,
exhaustive aggregate corpora, and extended recovery:

```bash
python3 scripts/qualify_exact_artifact.py \
  --source-sha "$(git rev-parse HEAD)" \
  --test-tier slow
```

Run the complete public suite at one clean exact head with the pinned browser
already installed:

```bash
uv run python scripts/run_test_tier.py complete \
  --source-sha "$(git rev-parse HEAD)"
```

That command validates source identity and cleanliness before accepting source
results, verifies the browser payload, runs the fast and browser cohorts, then
builds one exact wheel and runs both the required installed cohort and slow
cohort against it. The `fast`, `browser`, and `slow` source cohorts are disjoint
by directory. The required installed cohort intentionally repeats a small fast
subset because its distinct invariant is execution through the installed wheel.
Each cohort has a path-free count and identifier digest available through
`scripts/run_test_tier.py <tier> --list`; an empty or unimportable cohort fails
closed.

The exact-artifact procedure requires a clean checkout at the supplied source SHA, snapshots
the committed source, disables the `uv` build and install cache, validates the
wheel's bounded archive and `RECORD` before installation, seeds a new
environment from all locked dependency groups, and installs the explicit wheel
pathname without dependency resolution. It proves the wheel and installed
distribution payloads are byte-identical, validates the installer-updated
`RECORD`, rechecks the whole-wheel hash, and runs the selected installed tier with
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

The required suite includes a 45-item continuous-review restart cohort across
three libraries. It retains the cross-library fairness, interruption, forced
restart and recovery, authenticated progress and completion, exact installed
console, and zero target/media/artifact write assertions while remaining a
continuous pull-request gate. The full 1,000-item workload is an explicit slow
exact-wheel qualification with the same behavioral assertions plus its
throughput and 512 MiB resident-memory ceiling. Its timer and RSS sampling cover
the actual synthetic report, proposal, and admission workload before the
installed-console restart proof; the later no-change scan is not used as a
proxy for processing cost:

```bash
python3 scripts/qualify_exact_artifact.py \
  --source-sha "$(git rev-parse HEAD)" \
  --test-tier slow
```

Run that command from a clean exact-head checkout. The slow module is
owned by `tests/slow/test_*.py` and is not required by the public
pull-request gate; this is an explicit test tier, not a skipped test. A separate
manual or scheduled workflow is not maintained because the local exact-wheel
entry point supplies the required milestone evidence without adding another
remote workflow surface. Qualification reports distinguish a bounded subprocess
timeout (`*_timeout`) from a genuine nonzero test result (`*_failed`) without
including captured output or checkout paths.

## Timing baseline and tier decision

On exact `main` source `6405ff514f4b33498a7c907cf540a651b7f2ccb9`, the
former serial local discovery took 132.42 seconds: unit 77.37 seconds,
integration 34.94 seconds, browser 18.28 seconds, and architecture 1.72 seconds.
One exact maximum-shape resource test accounted for 38.56 seconds of the unit
cohort; it now lives unchanged in the slow tier. The corresponding hosted
`main` gate took 4 minutes 51 seconds including checkout, dependency, browser,
wheel, and test setup. These measurements are a compact baseline, not a
machine-specific pass threshold. On the tiered candidate, fast source took
74.07 seconds, browser 17.92 seconds, and the unchanged slow resource proof
37.16 seconds locally; the two routine source jobs can run independently. The
tiered gate runs fast source, installed
smoke, and protected browser as independent required jobs while retaining the
stable aggregate `public-gate` result.

A test may move out of routine qualification only when its expensive property
is scale, endurance, exhaustive breadth, or resource measurement and a smaller
required test preserves the same functional path and failure transitions. A
slow test is never silently skipped: delivery documentation identifies when
exact-head slow evidence is required. Fixed diagnostics distinguish source
provenance, setup/build failure, nonzero test failure, and subprocess timeout;
captured subprocess streams remain bounded and are not exposed in failure
evidence.

The public gate first installs the Chromium revision bound to pinned Playwright
1.61.0 and verifies its repository-bound complete payload digest before launch.
The installed-artifact suite then starts only disposable HTTPS review
servers with synthetic credentials and databases. Its browser cohort exercises
the complete three-action decision round trip at the primary desktop viewport,
the failure/retry and hostile-text path at 390 pixels, automatic-only detail,
private storage/cache and CSP, bounded DOM/assets, keyboard/focus, interaction
latency, restart, and a fresh authenticated browser context. Browser downloads
and test artifacts are not packaged or published.

The suite covers:

- metadata-policy Preview default/entry and exact canonical replay; closed
  candidate, keep-current, ignore, manual, abstention, conflict, and unsupported
  outcomes; exact case/report/proposal/target/policy/rule/catalogue/evidence/
  qualification identity binding; deterministic chronology beside human
  history; whole-read failure on malformed persistence; one-MiB limit-50 sample
  proof with 256 citations and 256 combinations per field; and the hard stale
  race where the sole old projection row is running and is atomically requeued
  for the current report without publishing an outcome. The protected browser
  proves owner/CSRF entry, unchanged Library work, non-confirmable leave impact,
  the selected B notebook, sticky desktop chapters, visible pinned compact
  mobile selector, responsive width, and non-authoritative language. The matrix
  also proves Review rejects Automatic with a stable problem, Preview permits
  its full supported policy workflow, only Automatic becomes Ask for human
  admission, default AI packets still exclude ignored proposals, and the F19
  projection records every one of the seven dispositions. A persisted non-empty
  browser sample must render without stray `undefined` text or page errors;

- authenticated, CSRF-bound review preparation requests and value-free compact
  progress; persisted request coalescing, exact-schema restart recovery,
  isolated progressive first-refresh catalogue baselines, one-snapshot
  aggregate activity status, one-lane retained-deadline scheduling, discarded
  superseded Library refresh responses, item-scoped currentness across unrelated library
  refreshes, complete successful scans before absence
  tombstones or ineligible retirements, failed-scan staged-retirement discard,
  reappearance/readmission, synchronized shared provider pacing, Linux
  same-process derived database authority, typed proposal-currentness drift,
  two-repository proof that slow external currentness holds no SQLite writer
  lock, final single-file eligibility drift and stale rediscovery without report
  or case publication, confirmation discovery before staged retirement,
  reappearance cancellation, failed-rediscovery child isolation, admission-time
  typed not-found handling, and Library projection refresh on material progress,
  per-work stage progression, every-lane completeness before proposal,
  atomic report/checkpoint rollback and restart,
  bounded third-attempt crash recovery, completed-ledger inert recovery, and a fixed
  two-lane proof that one slow work cannot impose a library-wide stage barrier;
  automatic first-start and interval admission, durable pause/resume, explicit
  terminal retry, action-woken terminal-failure scheduling, deadline admission
  between bounded work batches, cross-library ageing and reserved-new-work fairness, bounded
  circuit recovery, canonical material-source fingerprints including root-cover
  field presence and raw-state distinctions, one validated repository open per
  claimed stage, atomic output-plus-transition-plus-next-claim crash recovery,
  report-publication provider disposition, no-case admission completion, indexed
  aged and recent claim ordering, and
  an exact installed-`review-serve` 45-work three-library first-start,
  forced-restart, authenticated-progress, completion, and zero-write proof in
  the required gate. The explicit slow tier repeats the complete behavioral
  proof over 1,000 works and retains the throughput and memory-ceiling assertions;

- closed reports, canonical serialization, stable claim/report references,
  title normalization, and occurrence-local ISBN applicability, including
  explicit non-ISBN scheme and URI controls across package, opening, and rear
  evidence; controlled contributor-role separation, compound roles, contextual
  production-credit negatives, and typed manifestation/work/copyright/
  creation/modification/conversion date events at exact year/month/day/signed-
  timestamp precision across package, front, and rear evidence;
  paired source-explicit subtitle/publisher/imprint recognition across front
  and rear indicia, distinct publisher/imprint kinds, exact excerpts and rear
  provenance, conflict/repetition retention, exact bounds, hostile-context
  isolation, canonical replay, installed database reopen, and rejection of
  prior `epub-opening-v2` citations under `epub-opening-v3`;
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
  qualification, typed taxonomy/publication scope, strict record/identity/join
  failure, field-local optional failure, and identity order. The shared
  provider matrix covers every closed field state, mixed-validity scalars and
  collections, valid-member source order and duplicate occurrences, broken
  optional links, whole-field/no-prefix collection overflow, exact cardinality
  boundaries, work/edition scopes, controlled roles, evidence source ordinals,
  and bounded parser/schema diagnostics counted once. Hardcover coverage
  additionally fixes both
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
  request order, shared one-second pacing, the complete closed rich-evidence
  allowlist with Search/work/edition scope separation and paired author
  identity behavior, terminal
  failure mapping, identified metadata/cover requests, cover-failure isolation,
  provider-order/contact privacy, one retry per request, exactly four retries
  and 49 attempts per observation, terminal 429, 503-only `Retry-After`,
  pacing/backoff/deadline/byte/cancellation bounds, and the explicit five-item
  batch envelope;
- Google Books keyed and credential-free fixed-origin requests, disabled
  zero-egress, exact ISBN/work qualification, unresolved work linkage,
  evidence-only publication strings, deterministic exact-ID replay and
  provider order apart from their independently captured access times,
  response/candidate/request/aggregate bounds including six attempts per query
  and 54 per observation, quota/4xx/503/no-match typing, canonical
  `Retry-After`, accepted/rejected transport classes, deadline and partial-byte
  bounds,
  field-local scalar and collection failures, strict volume identity and
  strict recognized `industryIdentifiers` candidate shape, with silent
  omission of well-formed unsupported identifier types,
  cancellation/timeout behavior, and credential/raw-response redaction;
- strict `batch-manifest-v5` replay and clean-current rejection, with exact
  54/27/49 Google Books/Hardcover/Open Library metadata-attempt maxima, one
  shared pacer per paced adapter across foreground entries, and no persisted
  attempt diagnostics;
- deterministic `metadata-report-v22` serialization and database reopen preserve
  claim raw order, rich-evidence canonical order, duplicate source occurrences,
  source ordinals, scopes, roles, field states and diagnostics byte-for-byte.
  Application tests merge parser, attempt and incomplete-outcome diagnostics
  once before persistence and prove reopen does not inflate them. Persistence
  rejects `metadata-report-v19` before mutation and a direct regression proves
  the prior database bytes remain unchanged;
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
result under `fixed-conservative-v6`. Persistence tests reject prior proposal
documents and proposal-store table shapes, including `fixed-conservative-v5`,
without mutation, then prove exact deterministic replay under the current
policy. Every synthetic EPUB member has a fixed timestamp so the probe identity
does not depend on the qualification wall clock. No live library or provider is
used in development or CI.

Focused publication-date tests cover exact-day refinement, shared-month
projection, different-month year fallback, different-year conflict, independent
correlation and reference closure, and canonical proposal/packet replay.
Private logical qualification routing between `publishedDate` and
`publishedYear` is tracked separately from the product component.

Focused field-policy tests cover ISO 639-1/639-2 aliases, compatible regional
preservation, base/script/region contradictions, malformed language controls,
publisher/imprint/brand/group statements, wrong-scope and malformed publisher
controls, target non-independence, and canonical proposal/case/packet replay.
Private acceptable-action adjudication and scoring are not product-policy
tests.

Private shadow-review qualification follows the separate
[execution and action-scoring contract](reviewer-qualification.md). It freezes
the compiler catalogue, treatment, cohort, order, and scorer before fresh
scoring; reports first-pass, retry-pair, execution-inclusive ITT, and terminal
semantic views separately; and scores only exact catalogue actions. Ordinary
timeouts and semantic outcomes are terminal observations. Only predeclared,
bounded process, transport, cancellation, resource, or receipt-integrity
failures may receive an append-only paired retry. Development and CI use no
live library or provider for this harness; any explicitly authorized private
provider qualification remains synthetic or irreversibly sanitized.

Focused bibliographic-relationship tests cover title-to-subtitle and
title-to-series movement, clean versus decorated titles, exact-component
duplication vetoes, ambiguous component and localized/original work titles,
edition/work scope disagreement, no-safe-combination fallback, canonical
proposal/case/packet replay, clean-schema persistence, exact currentness, and
result-v2 rejection of a per-field tuple that is not one allowed combination.

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

The #377 synthetic indicia aggregate reports target-work subtitle, publisher,
and imprint recall separately from explicit-negative precision. Paired
front/rear fixtures cover semantic and exact-labelled admission, while rear
fixtures cover the exact excerpt bound, conflicts, repetition, hostile
copyright-holder/printer/distributor/retailer/conversion contexts, and
advertisement/preview/other-work isolation. Focused regressions additionally
cover nested single-emission, multiple exact sibling labels, mixed complete and
split labels in one ancestor, repeated equal complete/split siblings, multiple
assembled labels, scanning/production/workflow label values, promotion cues
inside labels, the exact 16-fact rear cap, front/rear NFC and whitespace
normalization, `metadata-report-v22`
canonical replay, clean database creation, and byte-unchanged rejection of v13
state. Only aggregate expected, found, and false-positive counts are asserted
or exported; fixture rows and source text remain private.

Comparison regressions cover every closed method and its near misses, NFC
without accent stripping, typographic mapping, variant markers, complete
multi-author count/order/role/scope, conservative single-author comma
inversion, structurally bound aliases, ambiguous or malformed bindings,
canonical replay and tamper rejection. Provider/application tests also prove
the finalizer adds no request, performs only the defined publication-fact
promotion/demotion and canonical ordering, and does not change proposal or
reviewer admission authority.

Provider-date regressions use synthetic year, month, day, and signed-offset
timestamp values. They prove lexical/normalized replay, closed event and
linkage typing, exact declared-versus-inferred source precision, no precision
invention, destination-exact manifestation date/year projection,
non-selectability of unresolved publication text, explicit conflict choices,
and reviewer citation coverage without exporting real-library values.
Reviewer replay cases reject missing or opaque-only linkage, identifier
fact/reference disagreement, forged kind/value/applicability/scope/raw-order
or provider/candidate membership, invalid ISBN/ASIN syntax, private or
credential-shaped context, missing rich-date-evidence linkage, malformed
identifier and format shapes, duplicate/reordered facts, and context beyond
either 16-fact bound. Request-v9 replay exercises the same packet-v5 validator
and recompiles the exact packet-only action catalogue. Tests cross positive
candidate actions into destinations that previously produced only manual
outcomes and negative actions into historically feasible destinations, then
vary evidence scope, manifestation binding, support, citation closure,
publication precision, and bibliographic combinations independently. Unknown
obligations fail closed; action and exclusion identities remain stable when an
opaque review-case digest changes.
Three independently correlated complete 16-author representations retain all
48 role facts under the correlation-derived aggregate bound, while an
over-bound forged role array still fails closed. Persistence records and
revalidates the exact request-v9 discriminator; a prior request-v8 table is
rejected without changing its database bytes. Reviewer adapter tests
accept insignificant raw-result JSON formatting while retaining strict UTF-8,
duplicate-key, shape, identity, citation and semantic failures. Synthetic
two-combination tests prove that every compiled action remains schema-reachable
but mutually incompatible bibliographic actions never share a recommendation
branch.
Populated non-bibliographic current fields remain marked target-only when
competing provider evidence requires manual review without an evidence-backed
preservation candidate. Actionable provider-cover candidates count their exact
closed cover-provenance correlation as independent provider support.
Access-
time regressions inject fixed clocks at every metadata-provider boundary,
exercise successful and terminal post-dispatch statuses, prove absent time for
no-input, overflow, invalid-clock, and expired pre-dispatch paths, and verify
canonical serialization, database/report reopen, proposal derivation, and
reviewer projection never refresh the stored value.

Hostile-input tests name a supported external actor and reachable boundary.
Typed in-process collaborators are not attacker-controlled unless the product
explicitly introduces an untrusted extension seam.

Metadata-policy activation tests use clean-schema SQLite stores and synthetic
reports to exercise the transaction boundary, restart while a fixed transition
is processing, exact response replay, consecutive generation gating,
exact-current cohort exclusion, stale old-policy publication, historical
resolved-destination replay, progressive completion, missing F17a linkage,
actual terminal extraction/provider failure and safe stage-specific retry, and
transition-scoped acknowledgement. Regression coverage also binds configured
generation-one overrides through admission, keeps new-library preview separate
from fail-closed proposal execution, restarts pre-existing terminal work, and
checks atomic extraction-publication transition failure. Generated
OpenAPI and the authenticated browser flow cover activation and transition
routes. F14 builds and installs the exact-head wheel outside its source snapshot,
then uses that installed adapter only against its owned disposable synthetic
library. Its receipt hashes the real validated event-ledger sequence rather than
a derived operation summary. F17 additionally qualifies the installed continuous
composition against owned disposable Audiobookshelf 2.26 and latest containers,
using six to eight rights-verified real EPUBs. It round-trips every existing
destination across mixed human and qualified automatic origins and proves
field-local drift, one fair instance slot, pause/revoke, restart, ambiguity,
manual hold, policy demotion, scoped suspension, audit and cover cleanup. Raw
case identities, metadata, paths and media remain private; public evidence is
aggregate only.

Guarded-application tests use only synthetic immutable decisions and fake
backup, capability, exact authority binding, distinct consent, dependency,
clock, target, fault, and cover
recovery adapters. They cover both origins, every closed destination shape,
atomic series, no-ops, ordered gate failures, pause/supersede/claim semantics,
sibling independence, atomic revoke-versus-claim races, persisted ambiguous
desired/pre/third readback and forward reevaluation, lineage-replacement races,
bounded pending-action and cleanup restart runners, retry exhaustion, restart
boundaries, validated gap-free append-only audit retrieval, durable connection-
and destination-scoped capability suspension including atomic claim races,
retryable owner-only cover cleanup, canonical
row/document/action corruption rejection including boolean/integer aliases and
closed failure/token types, architecture imports, authenticated API calls, and the
absence of ordinary-runtime Audiobookshelf/media-write construction.
## Guarded-operation qualification

F15a qualification covers absent and exact-current startup, ready/claimed/
applying/verifying/reconciling restart, graceful claim drain and pause/revoked
admission, bounded timing retention, incident aggregation and narrow conflict
projection, private cover recovery survival/cleanup and permission/capacity
failures, support preview/export redaction and size/depth bounds, static
architecture and clean-schema checks, an exact installed container, and the
unchanged authenticated report/review browser suite.
