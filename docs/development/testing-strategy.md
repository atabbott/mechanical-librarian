# Testing strategy

Audiobookshelf write qualification uses synthetic disposable libraries and API
keys only. It proves one metadata/tags PATCH and one digest-verified multipart
cover upload at v2.26.0 and the then-latest stable image, including read-back,
refusal and ambiguous-response reconciliation. With the frozen private operator
corpus it also exercises the installed application against two real disposable
libraries: supported preparation, owner decisions, exact-policy consent,
continuous scheduling, pause/resume, idempotent rescan, external-drift
regeneration and restart recovery. Provider evidence is supplied by a bounded
in-process HTTP fake derived from each request's local title and author evidence;
the qualifier never contacts a production provider or library and never uses
direct database or browser writes.

Run the canary from a clean exact head. It archives that head before importing
or running the adapter, creates scoped disposable API-key users, deletes those
keys during teardown where the service remains available, and removes only its
labelled container. Its `audiobookshelf-field-effect-v2` aggregate receipt adds
an `installed_continuous` lifecycle summary to the image digests and field
outcomes, never credentials, user tokens, passwords, source metadata or image
bytes:

```bash
uv run python scripts/qualify_audiobookshelf_field_effect.py \
  --source-sha "$(git rev-parse HEAD)"
```

That generated diagnostic canary is not milestone acceptance. F17 milestone
qualification supplies the frozen private operator corpus explicitly and must
report `operator_corpus: true` with six to eight cases:

```bash
uv run python scripts/qualify_audiobookshelf_field_effect.py \
  --source-sha "$(git rev-parse HEAD)" \
  --corpus /private/path/to/frozen-rights-verified-epubs
```

The public suite has four explicit qualification tiers. They retain every
existing assertion while keeping independent feedback paths parallel and
diagnosable.

pytest is the execution engine beneath those tiers, not a second entry point
beside them. `scripts/run_test_tier.py` remains the only way a tier is run or
listed: it names each cohort's exact files, so a cohort still admits exactly
what its `COHORTS` patterns match, and it derives its cohort manifest,
`test-tier-stall-v1` diagnostic, and `test-tier-result-v1` summary from that
one pytest session. Test identities stay the dotted, path-free names
`unittest.TestCase.id()` produces, so a cohort digest is comparable across the
engine change. pytest runs `unittest.TestCase` natively and adopts unittest's
own `getTestCaseNames`, so an unconverted test file needs no change; the runner
additionally honours the `load_tests` protocol, which pytest does not
implement, so a module that selects a subset of its own namespace still
contributes only that subset. Two of the runner's pytest arguments carry tier
behaviour rather than taste: `--capture=sys` leaves file descriptor 2 alone so
the stall diagnostic still reaches an observer while the cohort's own output
stays discarded, and `--tb=native` keeps a bounded traceback's retained tail
ending at the assertion and the innermost frame.

A tier supplies its own arguments and refuses to run under ambient ones. pytest
incorporates `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD`,
`PYTEST_DEBUG`, and `PYTEST_DEBUG_TEMPROOT` on its own, and any of them can
change what a cohort runs or what it reports: `PYTEST_ADDOPTS='-k passing'`
reduces a cohort to its passing subset and still reports the tier passed. The
runner fails closed on all five, naming the variables and never their values,
which may carry private paths. `PYTEST_THEME` and `PYTEST_THEME_MODE` remain
permitted because they only colour a stream every tier discards.

Plugins are the same exposure one level over. pytest auto-loads every installed
distribution exposing a `pytest11` entry point, so a `pytest-randomly` or
`pytest-xdist` that happens to be in an environment could reorder, shard, or
drop cases with no variable set and nothing to reject. A tier passes
`--disable-plugin-autoload` and loads pytest's builtins and the runner's own
plugin only. A tool the suite genuinely needs is pinned in the `dev` group and
loaded explicitly, never inherited from whatever is installed.

Tier accounting follows unittest's, not pytest's report stream. A case that
raises `AssertionError` is a failure and anything else is an error, whichever
phase raised it. A class-scoped `setUpClass` or `tearDownClass` failure is one
class-level error that runs no tests, even though pytest reports it against
every collected method; an instance `setUp` failure remains one error per case.
An unexpected success refuses the tier, as `wasSuccessful()` did, and a tier
passes only when pytest's exit code and the runner's own accounting agree.

## Test economy

These are standing rules for every test written from here on, not a record of
one cleanup. They sit beside the `Code economy` rules in `AGENTS.md` and are
read the same way: the rule, the reason it exists, and the case where the
longhand stays.

- Express variant cases through `@pytest.mark.parametrize` or vitest's
  `it.each` rather than writing a near-duplicate method per variant. A variant
  is a value, not a behaviour, and a copied method hides which value actually
  failed. Case count is the invariant, never line count: collapsing four
  methods into one parameterized case with four parameters keeps four
  reported cases and four independent failures, while collapsing them into one
  method with a loop deletes three. `parametrize` does not work on a
  `unittest.TestCase` method -- it collects as a single case and then fails
  with a missing-argument `TypeError` -- so a `TestCase` class either converts
  or keeps `subTest`, which parameterizes the assertions without splitting the
  case. Where a variant needs its own name to be diagnosable, leave it a
  separate test and say why.
- Build fixture data through a shared builder rather than inlining the same
  payload in each test. A payload copied into thirty tests is thirty places to
  update when a schema field is added, and the copies drift into thirty
  slightly different documents that no longer agree on what the canonical one
  is. Shared builders live in `tests/support/` for the backend and
  `frontend/tests/fixtures.tsx` for the browser; a test module never imports
  another test module's fixtures, because that makes one cohort a dependency
  of another. Inline a payload only where its exact shape is the thing under
  test, and keep it inline where a builder parameter would exist for one
  caller.
- Force a failure through a seam that survives relocation, not by substituting
  a member on the object under test. Replacing a method on a live repository
  or service and then calling a sibling that invokes it works only while both
  members live on the same object. When the callee moves into an aggregate the
  call becomes internal, the substitution stops reaching it, and the test does
  not fail -- it stops testing, and stays green with its assertion silently
  removed. Prefer a fake collaborator, an injected port, or a connection
  substituted at construction, so the failure is forced across a boundary the
  code must keep. Where a private member genuinely is the contract under test,
  say so at the site.
- Drive a frontend debounce, dwell, or interval on `vi.useFakeTimers()`, and
  reach the assertions through `fireEvent` and `vi.waitFor` rather than
  `userEvent` or `findBy*`. `@testing-library/react`'s
  `jestFakeTimersAreEnabled()` reports true only where a global `jest` exists,
  so under vitest fake timers its `asyncWrapper` schedules a zero-delay timer
  on the fake clock and never advances it: every `userEvent` call and every
  `findBy*` waits forever instead of failing. `userEvent.setup({ advanceTimers
  })` does not rescue it, because the stall is in Testing Library's wrapper
  rather than user-event's own wait, and `test.globals: true` does not either
  -- which is why it is declined in `vite.config.ts`, and why `tsconfig.json`
  does not declare the matching ambient types: vitest injects `vi`, `describe`,
  `expect` and their siblings, never `jest`, so the detection stays false,
  while types for globals nobody enables turn a missing import into a run that
  fails after typechecking passed. Only a `globalThis.jest = vi` shim would
  flip the detection, and that would put every `waitFor` on Testing Library's
  fake-timer branch, where each poll advances the clock 50 ms and silently
  eats the interval under test. Wait with
  `vi.waitFor(until, { interval: 0 })` instead, which polls on the real clock
  and advances the fake one by nothing, so the advance the test writes down is
  the only time that passes. Carry a mutation check with each advance --
  shorten it and the test must fail -- and pin the advance to an effect the
  code after the awaited timer owns: absent a millisecond short of the
  interval, present on it. A timer-count delta is not that proof. It shows
  only that some timer expired, so a dwell that schedules its timer and then
  awaits a different one keeps the count and passes while suspending nothing.
  Where that effect does not surface in the scenario under test, prove the
  interval in a focused test that reaches it rather than weakening the
  assertion to the level at hand. Real timers stay only where no timer is
  being measured.

F19a adds focused source proof for clean-schema automatic bootstrap, exact
qualified option/bundle admission, atomic human/automatic uniqueness,
restart/idempotency/currentness, pause/suspension/recovery, and the selected
responsive policy notebook. The private semantic corpus remains private; the
public manifest is aggregate-only. The complete exact-artifact tier is the
authoritative cumulative 1,000-work/no-write demonstration.

The same job also gates the Python source on formatting, lint, and strict
typing before it runs any test, using the configuration committed in
`pyproject.toml`:

```bash
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src tests scripts
```

`ruff format` is the line-width contract; ruff's lint side selects pyflakes,
pycodestyle, bugbear, and import ordering; mypy is
strict over `src/`. Narrowings are explicit configuration with a stated reason,
and site suppressions are inline, named, and justified. See
[CONTRIBUTING.md](../../CONTRIBUTING.md) for the contributor-facing contract.

The fast routine source tier owns unit, focused integration, and static
architecture behavior:

```bash
uv run python scripts/run_test_tier.py fast
```

The same public source job uses exact Node 24.19.0/npm 11.17.0 to run the
frontend lock install with lifecycle scripts disabled, regenerate DTOs from the
committed OpenAPI document, typecheck, lint, run Vitest/Testing Library, build
the asset graph, and require a clean diff. Generated DTOs and assets remain
ignored. Before a candidate is accepted, two fresh output directories must be
byte-identical and `npm audit` must report no known vulnerability in the exact
lock.

The required exact-installed-artifact tier owns wheel identity, archive and
installed `RECORD` integrity, installed imports, generated package/schema
integrity, the installed console, and a representative end-to-end lifecycle:

```bash
python3 scripts/qualify_exact_artifact.py \
  --source-sha "$(git rev-parse HEAD)" \
  --test-tier required
```

The protected browser tier remains independently runnable. Install the Chromium
payload and record which one ran before accepting its result; the middle command
prints a `browser-payload-evidence-v1` record naming the platform, the Chromium
revision, the Playwright version and the payload's tree digest:

```bash
uv run playwright install --with-deps chromium
uv run python scripts/verify_playwright_browser.py
export ML_REACT_ASSET_DIR="$(mktemp -d)"
(cd frontend && npm ci --ignore-scripts --no-audit --no-fund && npm run generate \
  && ML_FRONTEND_OUT_DIR="$ML_REACT_ASSET_DIR" npm run build)
uv run python scripts/run_test_tier.py browser
```

The asset build is not optional. The tier reads `ML_REACT_ASSET_DIR` with no
fallback, so the two commands alone end in `KeyError` before any browser opens
-- which is what this block used to instruct. The assets go to a temporary
directory for the same reason the gate sends them to `RUNNER_TEMP`: building
them inside the checkout leaves untracked output behind, and a clean tree is
itself one of the checks.

The tier receives a freshly built `ML_REACT_ASSET_DIR` and opens the real hashed
shell through authenticated FastAPI direct entry to all five workspace routes.
It proves persistent no-refresh navigation, back/forward and durable URL state,
critical and progressive queries, cancellation and polling teardown, focus,
title and announcements, CSRF/build/currentness recovery, hostile rendering,
desktop and 390-pixel light/dark layouts, and clean console/page-error behavior.

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
closed. `scripts/run_test_tier.py --list`, with no tier, emits the
`test-tier-inventory-v1` document naming every tier the runner accepts, so the
documented tier set can be checked against the runner rather than trusted:

```bash
uv run python scripts/run_test_tier.py --list
```

The exact-artifact procedure requires a clean checkout at the supplied source SHA, snapshots
the committed source, disables the `uv` build and install cache, validates the
wheel's bounded archive and `RECORD` before installation, seeds a new
environment from all locked dependency groups, and installs the explicit wheel
pathname without dependency resolution. It proves the wheel and installed
distribution payloads are byte-identical, validates the installer-updated
`RECORD`, rechecks the whole-wheel hash, and runs the selected installed tier with
the isolated environment's interpreter. It then checks the PEP 610 install
origin and invokes the installed console command from outside the checkout.
For Mechanical Librarian, wheel admission additionally requires the generated
expected build identity, canonical React asset manifest, shell, and every and
only reachable hashed script/style. It rejects frontend source, generated DTOs,
`node_modules`, and source maps. The container repeats the frontend build in a
pinned Node stage and copies only the validated bundle into the Python build;
Node/npm are absent from the runtime stage.
The exact container lifecycle emits `container-lifecycle-evidence-v3` only
after the packaged loader accepts that bundle and an isolated final-image probe
finds no Node/npm executable, frontend/generated/`node_modules`/Vite directory,
package manifest, or source map.
Its path-free JSON evidence binds the source SHA, wheel SHA-256, payload
SHA-256, and console-output SHA-256. A dirty or different source, ambiguous or
changed wheel, invalid archive/record, reused destination, unlocked dependency,
origin mismatch, payload mismatch, stale test install, or non-installed
import/command fails closed. The installed console proof performs exact-ID
report and ProposalSet creation, concurrent admission, replay, restart-backed
get, successor/historical projection, and nonactionable retirement against a
disposable loopback target and synthetic EPUB.

The committed focused automatic no-change tests prove deterministic identity
across replay timestamps, closed domain bounds, exact-current schema admission,
and the existing scheduler/guarded-effect regression suite. Broader integration
scenarios remain covered by their established field-effect cohorts.

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
including captured output or checkout paths. A failed command's captured
streams are no longer discarded with the exception: the last 16 KiB of each is
reported as a separate narrative account on standard output, with the evidence
record still alone on standard error. That account carries the runner's own
paths, so it is never folded into the record. Before any captured byte is
reported, every value in the environment the qualifier constructed for the
child is replaced by its own variable's name where the variable is
secret-named, or where the value is long and is not a path: the qualifier
inherits the ambient environment, so a name vocabulary alone would miss an
opaque identifying value under an innocuous name.

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
tiered gate runs fast source, installed smoke, and protected browser as three
independent required jobs. They remain separate because the earlier serial
exact-installed workflow exhausted its 825-second subprocess deadline on four
consecutive candidates, including after internal test sharding. No runner-only
aggregate job is maintained; the trusted delivery controller must observe all
three named results at the exact candidate head.

A test may move out of routine qualification only when its expensive property
is scale, endurance, exhaustive breadth, or resource measurement and a smaller
required test preserves the same functional path and failure transitions. A
slow test is never silently skipped: delivery documentation identifies when
exact-head slow evidence is required. Fixed diagnostics distinguish source
provenance, setup/build failure, nonzero test failure, and subprocess timeout;
captured subprocess streams remain bounded and stay out of the failure evidence
record, reaching only the redacted narrative account beside it.

The public gate first installs the Chromium revision bound to pinned Playwright
1.61.0 and verifies its repository-bound complete payload digest before launch.
The installed-artifact suite then starts only disposable HTTPS review
servers with synthetic credentials and databases. Its browser cohort exercises
the complete three-action decision round trip at the primary desktop viewport,
the failure/retry and hostile-text path at 390 pixels, automatic-only detail,
private storage/cache and CSP, bounded DOM/assets, keyboard/focus, interaction
latency, restart, and a fresh authenticated browser context. Browser downloads
and test artifacts are not packaged or published.

Authenticated review qualification is split at the application boundary.
Application-operation tests exercise bounded case, Library, Attention,
preparation, session, decision, policy, Activity, effect, and support outcomes
without HTTP. Transport-contract tests then prove route DTOs, authentication,
CSRF, Host/Origin checks, private caching, currentness, idempotency, and typed
failure mapping against the same composed application. Architecture checks
reject handler-facing repository contracts or workflow construction, while
SQLite restart and concurrency tests remain the authority for atomicity.

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
- deterministic `metadata-report-v26` serialization and database reopen preserve
  claim raw order, rich-evidence canonical order, duplicate source occurrences,
  source ordinals, scopes, roles, field states and diagnostics byte-for-byte.
  Application tests merge parser, attempt and incomplete-outcome diagnostics
  once before persistence and prove reopen does not inflate them. Persistence
  rejects immediate prior `metadata-report-v25` before mutation and a direct regression proves
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

The exact-artifact suite exercises `review-serve` outside the checkout and proves
the source-only workbench is absent from the installed wheel and container.
Workbench integration tests use a disposable loopback target, synthetic media,
private test secrets, and an absent database. Unit proposal-policy coverage includes whitespace, case, and
width variants of the controlled package creator placeholder. The installed
proposal path uses a whitespace/case variant and requires a nonmutating authors
result under `fixed-conservative-v8`. Persistence tests reject prior proposal
documents and proposal-store table shapes, including immediate prior `fixed-conservative-v7`,
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

Focused contained-reviewer tests use fake processes and synthetic inputs to
prove private artifact/profile binding, authentication isolation, durable
pre-invocation attempt evidence, in-memory child-release gating, local schema
enforcement, bounded output, redaction,
timeout/cancellation cleanup, and exact child-process death. No live library,
provider, or production service is used.

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
normalization, `metadata-report-v26`
canonical replay, clean database creation, and byte-unchanged rejection of v23
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
Packet replay cases reject missing or opaque-only linkage, identifier
fact/reference disagreement, forged kind/value/applicability/scope/raw-order
or provider/candidate membership, invalid ISBN/ASIN syntax, private or
credential-shaped context, missing rich-date-evidence linkage, malformed
identifier and format shapes, duplicate/reordered facts, and context beyond
either 16-fact bound. Packet-v6 replay recompiles the exact packet-only action
catalogue. Tests cross positive
candidate actions into destinations that previously produced only manual
outcomes and negative actions into historically feasible destinations, then
vary evidence scope, manifestation binding, support, citation closure,
publication precision, and bibliographic combinations independently. Unknown
obligations fail closed; action and exclusion identities remain stable when an
opaque review-case digest changes.
Three independently correlated complete 16-author representations retain all
48 role facts under the correlation-derived aggregate bound, while an
over-bound forged role array still fails closed. Product persistence records
only deterministic shadow decisions/outcomes and revalidates their exact
current discriminators; no reviewer request/result lifecycle remains. The
source-only contained-reviewer tests separately retain strict UTF-8,
duplicate-key, shape, identity, output-schema, process, and trust-boundary
failures without adding product semantics. Synthetic
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
deterministic packet projection never refresh the stored value.

Hostile-input tests name a supported external actor and reachable boundary.
Typed in-process collaborators are not attacker-controlled unless the product
explicitly introduces an untrusted extension seam.

Metadata-policy activation tests use clean-schema SQLite stores and synthetic
reports to exercise the transaction boundary, restart while a fixed transition
is processing, exact response replay, consecutive generation gating,
exact-current cohort exclusion, stale old-policy publication, historical
resolved-destination replay, progressive completion, missing F17a linkage,
actual pre-package extraction/provider failure, complete mixed-provider
terminal reports, safe per-work retry, atomic package publication, and
transition-scoped acknowledgement. The matrix includes matched, no-match,
disabled, insufficient-input, ambiguous/over-bound, authentication,
unavailable, and malformed outcomes; mixed pagination and a 50-work synthetic
cohort prove exact counts and absence of a slow-work global barrier. Regression
coverage also binds configured
generation-one overrides through admission, keeps new-library preview separate
from fail-closed proposal execution, restarts pre-existing terminal work, and
checks atomic extraction-publication transition failure. Generated
OpenAPI and the authenticated browser flow cover activation and transition
routes. F14 builds and installs the exact-head wheel outside its source snapshot,
then uses that installed adapter only against its owned disposable synthetic
library. Its receipt hashes the real validated event-ledger sequence rather than
a derived operation summary. F17 additionally qualifies the installed continuous
composition against owned disposable Audiobookshelf 2.26 and latest containers,
mounting six to eight rights-verified real EPUBs. Temporary private copies
receive deterministic qualification metadata;
all required destination coverage is bound to those real-work service identities,
while a separate adapter canary supplies additional diagnostics. It round-trips every existing
destination across mixed human and qualified automatic origins and proves
field-local drift, multi-library progress, pause/revoke, restart, manual hold,
scoped suspension, audit and cover cleanup. Deterministic fake-service tests own
exhaustive ambiguity, retry exhaustion, policy demotion and scheduling-fairness
proof. Exact-current persistence and continuous-application source tests separately
prove restart recovery of accepted return-to-automatic requests, post-discovery
successor forcing, failure-path successor preservation and greater-than-one-page
release recovery. Raw
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
