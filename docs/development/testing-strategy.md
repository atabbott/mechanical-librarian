# Testing and quality strategy

Authorities: the `COHORTS` dict in `scripts/run_test_tier.py` for what each
tier runs, `.github/workflows/public-gate.yml` for what the gate runs,
[what merging proves](../../CONTRIBUTING.md#what-merging-proves) for which
checks are enforced and which are maintainer-manual, `pyproject.toml` for the
lint and typing configuration, and the tests themselves for what they assert.
This document states the invariants those artifacts must keep. It does not
enumerate what any cohort covers; that enumeration is the suite.

Quality means the supported outcome is understandable, bounded, secure,
recoverable within its documented envelope, and backed by exact evidence. A
change is done when its supported outcome and failures are documented and
tested, generated artifacts reproduce, gates pass at the exact head, required
reviews converge, tracking is correct, and no required work is hidden in an
untracked artifact.

## Tiers

The public suite has four explicit qualification tiers, declared in `COHORTS`.
The three source cohorts — `fast`, `browser` and `slow` — are disjoint by
directory. `required-installed` is not: it is the existing fast-source subset
plus one installed-only exact-wheel smoke, deliberately, because its distinct
invariant is execution through the installed wheel rather than a different set
of source tests.
`scripts/run_test_tier.py` is the only way a tier is run
or listed, so a cohort admits exactly the paths its declared patterns match.
`scripts/run_test_tier.py --list`, with no tier, emits a
`test-tier-inventory-v1` document naming every tier the runner accepts, so the
tier set can be checked against the runner rather than trusted. Each cohort has
a path-free count and identifier digest through `<tier> --list`; an empty or
unimportable cohort fails closed.

```bash
uv run ruff format --check src tests scripts   # the gate's source checks,
uv run ruff check src tests scripts            # run before any test
uv run mypy src tests scripts

uv run python scripts/run_test_tier.py fast    # unit, focused integration,
                                               # static architecture

python3 scripts/qualify_exact_artifact.py \    # wheel identity, RECORD
  --source-sha "$(git rev-parse HEAD)" \       # integrity, installed console,
  --test-tier required                         # representative lifecycle

uv run playwright install chromium             # protected browser tier
uv run python scripts/verify_playwright_browser.py
uv run python scripts/verify_playwright_browser.py --launch
export ML_REACT_ASSET_DIR="$(mktemp -d)"
(cd frontend && npm ci --ignore-scripts --no-audit --no-fund && npm run generate \
  && ML_FRONTEND_OUT_DIR="$ML_REACT_ASSET_DIR" npm run build)
uv run python scripts/run_test_tier.py browser

python3 scripts/qualify_exact_artifact.py \    # scale, endurance, resource
  --source-sha "$(git rev-parse HEAD)" \       # ceilings, exhaustive corpora
  --test-tier slow

uv run python scripts/run_test_tier.py complete \   # every test tier at one
  --source-sha "$(git rev-parse HEAD)"              # clean exact head
```

`complete` runs every **test** tier and nothing else. It verifies the source is
that exact SHA and clean, runs `fast`, verifies the browser payload, runs
`browser`, then hands the exact-artifact qualifier the required-installed and
slow tiers against one built wheel. It does not run ruff, mypy, the frontend
typecheck, lint or Vitest suites, the licence inventory, `npm audit`, or the
reproducible-build comparison. A head that passes `complete` is not a
qualified head: the source gates are the first three commands above, and
[what merging proves](../../CONTRIBUTING.md#what-merging-proves) is the full
list.

The browser asset build is not optional: the tier reads `ML_REACT_ASSET_DIR`
with no fallback, and building into the checkout would leave untracked output
where a clean tree is itself a check.

A test may move out of routine qualification only when its expensive property
is scale, endurance, exhaustive breadth, or resource measurement and a smaller
required test preserves the same functional path and failure transitions. A
slow test is never silently skipped: delivery documentation identifies when
exact-head slow evidence is required.

The continuous-review slow qualifier advances 2,000 deterministic synthetic
works through an exact installed-wheel in-process application, then checks a
separate `review-serve` restart. Its deterministic callbacks replace only
target/media/provider observation while preserving the production action queue,
SQLite publication, proposal, admission, projection, and foreground
serialization boundaries for all 2,000 works. One fixture-owned title proposal
differs from its current value so persistent Chromium proves early availability,
a bounded report-only choice at peak, and durable Activity/case readback late.
The independent direct 2,000-work control remains the clean completed/zero-
blocked proof for fairness, provider recovery, and full-stage throughput. The
installed path controls foreground response budgets and RSS; the retained CLI
control proves composition, shutdown, restart, and readiness. It holds one
discovery request at zero progress and one deterministic extraction at actual
early, peak, and late progress.
Chromium must be installed and verified with `uv run playwright install
chromium` and `uv run python scripts/verify_playwright_browser.py` before that
slow proof. Its synthetic fixture and installed process never use a live
library, provider, or private work identity. The retained receipt contains only
phase fractions, route-class sample counts, p50/p95/max, 5xx/transport totals,
computed quiescent-relative budgets, reconciliation elapsed time and
throughput, terminal successful-transition totals, fairness positions, restart
facts, and RSS; it never records routes, identifiers, paths, SQL, credentials,
or request values.

The same source job runs the frontend on the exact pinned Node and npm
toolchain; [what merging proves](../../CONTRIBUTING.md#what-merging-proves)
lists those commands against the workflow that runs them.

## What the gate proves

Every pull-request candidate runs independent required `fast-source`,
`exact-installed`, and `protected-browser` jobs. They are deliberately not
recombined: the former serial exact-installed workflow exhausted its 825-second
subprocess deadline on four consecutive candidates, including after internal
sharding. No runner-only aggregate job is maintained; the trusted delivery
controller must observe all three exact-head results. The same workflow runs on
push to `main`, so every merged commit is verified and two individually green
candidates cannot break `main` together in silence. Each job checks out
`github.event.pull_request.head.sha || github.sha`, so exact-head semantics hold
on both events, and only pull-request work is cancelled by a superseding head. A
fourth `container-image` job builds the shipped image at that same revision, so
a Dockerfile regression fails the gate; it never runs, loads, or pushes the
image.

`protected-browser` restores the Chromium payload from a cache keyed on the
installed Playwright version and saves it only after
`scripts/verify_playwright_browser.py` accepts it, so a malformed payload is
never persisted and every run describes the payload it used. That script records
the payload's tree digest rather than comparing it to a stored constant: the
constant asserted only that the payload matched whatever a maintainer observed
first, which an auto-updating Playwright re-recorded on every bump while
blocking each one until two platform hashes were regenerated by hand. It still
rejects a payload that is absent, symlinked, empty, or laid out unexpectedly,
and an unsupported platform. The accepted cost is that corruption of an
already-installed payload, within one Playwright version, is no longer detected.

`scripts/qualify_exact_artifact.py` proves that what merging ships is what the
source says. It builds from a clean checkout at the supplied source SHA with the
uv caches disabled, validates the wheel before installing it, seeds a new
environment from all locked dependency groups, installs the explicit wheel
pathname without dependency resolution, and then runs the selected tier through
that installed distribution's own interpreter, from outside the checkout, with
the source-only workbench proved absent. Its path-free JSON evidence binds the
source, wheel, payload, selected tier, and command-output digests. Every step is
fail-closed: a dirty or different source, an ambiguous or changed wheel, an
invalid archive or `RECORD`, a reused destination, an unlocked dependency, an
origin or payload mismatch, a stale test install, or a non-installed import or
command stops the qualification. The script is the authority for each check and
the order it runs in.

Public CI is necessary but is not merge authority. The maintainer-owned
exact-head controller and its tracking reconciliation remain authoritative, and
neither is a GitHub workflow: `public-gate.yml` is the only workflow this
repository has, and the controller's closing-reference, `tracking-exempt`, and
post-merge checks run outside it. Functional slices are reviewed and qualified
at the exact candidate head; tier-2 work adds independent safety and challenger
review plus receipt-bound aggregate qualification.

## Diagnostics

These are the invariants; the constants, the windowing and the reasons for both
are documented at their definitions in `scripts/run_test_tier.py` and
`scripts/qualify_exact_artifact.py`.

- A failure is diagnosable from the diagnostic stream alone. Fixed identities
  distinguish source provenance, setup or build failure, a nonzero test result
  and a bounded subprocess timeout, and a stalled test emits one bounded
  path-free identifier so a hosted timeout stays diagnosable.
- A failing tier reports every failed identifier — the runner never truncates
  that list — and each failing test's traceback under a per-test and
  whole-stream byte bound. Any truncation declares what it dropped, and what is
  retained holds the error identity as well as the tail, because `--tb=native`
  puts the identity in the middle of a long report.
- When the exact-artifact qualifier runs a tier as a child, it keeps a bounded
  tail of each captured stream, so a verdict longer than that window loses its
  leading identifiers while keeping the reason, status, counts and tier. The
  complete list is on the runner's own standard output in that case. The
  qualifier does not parse the runner's schema to rescue it: coupling the two
  layers would buy back one list and cost the independence that lets either be
  changed alone.
- Nothing carrying a path or a value reaches an evidence record. Raw cohort
  output stays discarded; a failing command's captured streams reach only a
  narrative account beside the record, on standard output, with every
  constructed-environment value replaced by its variable's name where the
  variable is secret-named or the value is long and is not a path. The
  machine-readable `test-tier-result-v1` and `test-tier-stall-v1` lines keep
  standard error to themselves.

## Runner invariants

pytest is the execution engine beneath the tiers, not a second entry point
beside them. Test identities stay the dotted, path-free names
`unittest.TestCase.id()` produces, so a cohort digest is comparable across the
engine change, and an unconverted `unittest.TestCase` file needs no change: the
runner honours `getTestCaseNames` and the `load_tests` protocol pytest does not
implement, so a module that selects a subset of its own namespace still
contributes only that subset.

A tier supplies its own arguments and refuses to run under ambient ones. The
runner fails closed on every pytest environment variable that can change what a
cohort runs or what it reports, naming the variables and never their values, and
passes `--disable-plugin-autoload` so an installed `pytest11` distribution
cannot reorder, shard, or drop cases. `scripts/run_test_tier.py` holds the exact
list and the reason each entry is on or off it.

Tier accounting follows unittest's, not pytest's report stream: `AssertionError`
is a failure and anything else is an error, a class-scoped fixture failure is one
class-level error that runs no tests, an unexpected success refuses the tier, and
a tier passes only when pytest's exit code and the runner's own accounting agree.

## Coverage

No coverage percentage is enforced, and none is planned before 1.0. The suite's
test-to-source line ratio already exceeds 1:1 and the gate's own quality is
high, so a threshold would add ceremony without adding protection: it would
measure lines executed rather than behaviour proven, and the failure this
project keeps finding is a check that stopped checking what it claimed, which a
percentage cannot see. Revisit the decision before 1.0. Until then, a change
that adds a code path adds the tests that prove it, and review is what enforces
that. (2026-08-15)

There is no separate contract, security, or documentation cohort, so the
security behaviour a change introduces is proven inside the unit and integration
cohorts beside the rest of it. Tests, types, safety gates, and failure handling
are never weakened to make a change pass.

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

### Measuring whether a test earns its keep

Neither instrument below is a gate. No coverage percentage and no mutation
score is enforced, and the decision recorded under `Coverage` above is
unchanged; these are recorded analysis, run when someone wants evidence about a
particular module rather than on every candidate. Both reproduce from these
commands. `mutants/` is generated output, ignored by git, and never committed.

```bash
uv run python -m coverage run --branch \        # whole-tier line and branch
  --source=src/mechanical_librarian \           # coverage, minutes
  scripts/run_test_tier.py fast
uv run python -m coverage report --precision=1 --sort=cover

export PYTHONPATH="$PWD/mutants/src"            # the mutated package, never
uv run python scripts/generate_mutants.py       # the editable install
uv run python scripts/run_mutation_audit.py \   # pass 1: capped, fast
  --only application.review_web --max-tests 40 --out pass1.jsonl
uv run python scripts/run_mutation_audit.py \   # pass 2: the unkilled set
  --only application.review_web --max-tests 0 \
  --survivors-from pass1.jsonl --out pass2.jsonl
```

`[tool.mutmut]` in `pyproject.toml` names the modules under audit. `PYTHONPATH`
is load-bearing: the editable install points at `src/`, so without it the tests
import unmutated source and every mutant is reported as surviving — a total
failure that looks like a clean result. `scripts/generate_mutants.py` documents
why it copies the package itself and why it collects stats without `-x`.
`mutants/tests` and `mutants/mutmut-stats.json` are snapshots taken when it
runs, so a test written afterwards is invisible to the driver and the mutants
it was written to kill are reported as surviving; `run_mutation_audit.py`
refuses to start in that state rather than measure the previous suite.

Each run appends one JSON line per mutant to its `--out` stream and fsyncs
before starting the next, so an interrupted run keeps what it executed, and a
rerun skips mutants already recorded rather than repeating them.

**Two passes, because a truncated test set over-reports survivors.**
`--max-tests` caps how many of a mutant's reaching tests are run. That is safe
for a `killed` verdict — one test failed, and truncation cannot un-fail it —
and unsafe for `survived`, because the killer may be a test the cap dropped.
Only pass 2's survivors are unkilled. In this suite's own audit a cap of 40
reported the login throttle as unpinned; the test that pins it was 48th of 51,
and three of the headline findings evaporated once the cap was removed.

**A batched run cannot say which tests kill nothing.** Under `-x` only the
first failing test is observable, so the `first_failure` field names one killer
and says nothing about the others. Four tests recorded zero kills that way and
each killed 83–90 mutants when measured alone. To judge one test, run it by
itself against every mutant it reaches:

```bash
PYTHONPATH="$PWD/mutants/src" uv run python \
  scripts/mutation_kills_for_test.py 'tests/unit/test_x.py::test_y'
```

That is the only measurement that may be cited as evidence a test kills
nothing.

Two further limits are properties of the instruments, not of the suite. A
coverage run on a developer host is not the coverage the product ships with:
`sys.platform` branches leave Linux-only code unexecuted and their tests
skipped, so a module can read far below its real figure. And a test that reads
source as text rather than importing it — the `tests/architecture` cohort —
cannot kill a mutant at all, because every mutant lives in the file it reads.
Absence of kills there is the instrument's silence, not the test's.

Some survivors are accepted rather than chased. A mutation of a `help=`,
`metavar=`, or `description=` string changes no behaviour, and an error
identity that no seam exposes — `_Parser.error` raises one fixed `ValueError`
that `interfaces/cli`'s `main` catches regardless of its message — cannot be
observed without pinning a string no operator ever sees. Record those; do not
write a test for them.

**A snapshot can also be taken against the unmutated package.** A path
entry that does not exist when the interpreter starts is cached as `None` and
is never revalidated, so creating `mutants/src` inside the run does not by
itself make it importable. On the first generation in a tree with no
`mutants/`, the stats phase therefore imported the unmutated package, the
trampoline never fired, and the map came back empty — which
`run_mutation_audit.py` would have reported as every mutant reached by no
test. `generate_mutants.py` now drops the import caches after creating the
directory and refuses to finish on an empty map, and the audit refuses to
start on one, because that failure is indistinguishable by inspection from a
module nothing covers.

**`reached by no test` is a claim about the map, not about the suite.** The map
is built from `tests/unit` and `tests/integration` less the three files
`[tool.mutmut]` excludes, and only from what runs in the collecting process. A
path a test drives through a subprocess, the installed console script, or a
container is invisible to it. `scripts/qualify_container_lifecycle.py` starts
the real container on every run, so everything `run_review_server` reaches on
the way to `uvicorn.run` is exercised there and appears nowhere in the map.
Establish what else exercises the code before reading a count as a gap.

### `bootstrap.composition`, judged rather than chased

Its 590 unreached mutants are not diffuse: they are 14 functions of 45, and
each was classified rather than sampled. 287 were unproved by anything and are
now covered by tests named for what they prove; the remaining 303 are accepted
here, and this is the record. (2026-08-18)

- `_IsolatedEpubInspector.inspect` and `.unchanged` (186) — the adapter around
  the EPUB inspector child. `tests/unit/test_epub_child.py` supplies generated
  real EPUB media to the isolated inspector, then proves rear series claims
  retain provenance through canonical report replay without a source path.
- EPUB series recovery is proved in the opening, child, and isolated-inspector
  cohorts with generated package/front/rear fixtures, decimal and
  multiple-membership positives, and explicit advertisement, catalogue, prose,
  promotion, and trailing-ambiguity negatives.
- `compose_continuous_guarded_application` (115) — composed unmocked by
  `run_review_server` on every started container, so the container lifecycle
  qualifier proves it end to end against a real SQLite store. This is the case
  the module was expected to be full of, and it is the only one.
- `_PublicationReceiptRepository.complete_review_extraction_publication` (2) —
  a `Protocol` body. Nothing can observe a mutation of it.

The 287 were not accepted because none of them had another prover.
`run_cover_review` (150) is a CLI subcommand no qualifier invokes; the five
`reopen_*` read seams, `create_proposal_set`, `run_metadata_report` and
`inspect_batch` (107) are bound only by the source-only workbench, whose own
test substitutes every operation and asserts the binding by identity; and
`compose_audiobookshelf_field_effect_capabilities` (30) is the untested twin of
a composer beside it that was tested. None of them was expensive, which is the
whole reason they are not on the list above.

## Fixtures, hostile input and privacy

Hostile files, network responses, configuration, credentials, SQLite rows, and
subprocess output are bounded and fail closed. Diagnostics are value-free when
rejected input may contain secrets, private paths, or media text. Synthetic
fixtures are repository-authored and contain no personal or copyrighted
material, and every synthetic EPUB member has a fixed timestamp so a probe
identity does not depend on the qualification wall clock.

A hostile-input test names a supported external actor and a reachable boundary.
Typed in-process collaborators are not attacker-controlled unless the product
explicitly introduces an untrusted extension seam.

No live library, provider, or production service is used in development or CI.
Public evidence from any qualification is aggregate only: raw case identities,
metadata, paths, media, private corpus rows, and credentials never leave the
private side.

## Qualification against a real service

Audiobookshelf write qualification uses synthetic disposable libraries and API
keys only. It proves one metadata and tags PATCH and one digest-verified
multipart cover upload at the two supported service images, including read-back,
refusal, and ambiguous-response reconciliation. Provider evidence is supplied by
a bounded in-process HTTP fake derived from each request's local title and
author evidence; the qualifier never contacts a production provider or library
and never uses direct database or browser writes.

Run the canary from a clean exact head. It archives that head before importing
or running the adapter, creates scoped disposable API-key users, deletes those
keys during teardown where the service remains available, and removes only its
labelled container. Its `audiobookshelf-field-effect-v2` aggregate receipt adds
an `installed_continuous` lifecycle summary to the image digests and field
outcomes, never credentials, user tokens, passwords, source metadata, or image
bytes:

```bash
uv run python scripts/qualify_audiobookshelf_field_effect.py \
  --source-sha "$(git rev-parse HEAD)"
```

That generated diagnostic canary is not milestone acceptance. Milestone
qualification supplies the frozen private operator corpus explicitly, with
`--corpus`, and must report `operator_corpus: true`. With that corpus the
qualifier also exercises the installed application against two real disposable
libraries: supported preparation, owner decisions, exact-policy consent,
continuous scheduling, pause and resume, idempotent rescan, external-drift
regeneration, and restart recovery.
