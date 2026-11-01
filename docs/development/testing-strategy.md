# Testing and quality strategy

This document says which test tiers exist, how to run each, what each proves,
and the rules every test follows. The artifacts are the authorities: `COHORTS`
in `scripts/run_test_tier.py` for what each tier runs,
`.github/workflows/public-gate.yml` for what the gate runs,
[what merging proves](../../CONTRIBUTING.md#what-merging-proves) for which
checks are enforced, `pyproject.toml` for lint and typing, and the tests for
what they assert. The suite, not this document, lists what each cohort covers.

A change is done when its supported outcome and its failures are documented and
tested, generated artifacts reproduce, and the gate passes at the exact head.
[Delivery and merging](slice-delivery.md) covers review and merge.

## Tiers

| Tier | Runs | Proves | Where |
| --- | --- | --- | --- |
| `fast` | `tests/unit`, `tests/integration`, `tests/architecture` | Behaviour, focused integration and static architecture rules | Gate: `fast-source` and three shard jobs |
| `browser` | `tests/browser` | The built React workspace in Chromium | Gate: `protected-browser` |
| `slow` | `tests/slow` | Scale, endurance, resource ceilings and exhaustive corpora, through the installed wheel | Maintainer-manual |
| `required-installed` | A fixed fast subset plus `tests/installed` | The code runs from the installed wheel, not the checkout | Gate: `exact-installed` |
| `probe` | `tests/slow/test_audio_probe.py` | Real audio probing with the image's own pinned `ffprobe` | Gate: `container-image` |
| `shipped-sqlite` | The `fast` and `slow` files that assert a query plan or a VM-step read budget | Those reads plan the same way on the SQLite the image ships | Gate: `container-image` |
| `complete` | `fast`, `browser`, `required-installed` and `slow` | One clean exact head passes them all | Maintainer-manual |

`fast`, `browser` and `slow` are disjoint by directory. The other cohorts
re-run files those three own, because what they add is an execution
environment, not tests: the installed wheel, the shipped `ffprobe`, or the
shipped SQLite, which plans some reads differently from a newer one. Wall-clock
budgets measure the host, not the engine, so they stay out of `shipped-sqlite`.

`scripts/run_test_tier.py` is the only way to run or list a tier. A cohort
admits exactly the paths its patterns match. `--list` with no tier prints a
`test-tier-inventory-v1` document naming every tier the runner accepts,
including `complete-installed`, which runs `required-installed` then `slow` in
the current interpreter. `<tier> --list` prints that cohort's path-free count
and identifier digest. An empty or unimportable cohort fails closed.

## Running the tiers

```bash
uv run ruff format --check src tests scripts   # source checks the gate runs
uv run ruff check src tests scripts            # before any test
uv run mypy src tests scripts

uv run python scripts/run_test_tier.py fast

python3 scripts/qualify_exact_artifact.py \    # build, verify and install the
  --source-sha "$(git rev-parse HEAD)" \       # wheel, then run the required
  --test-tier required                         # installed tier through it

uv run playwright install chromium             # browser tier
uv run python scripts/verify_playwright_browser.py
uv run python scripts/verify_playwright_browser.py --launch
export ML_REACT_ASSET_DIR="$(mktemp -d)"
(cd frontend && npm ci --ignore-scripts --no-audit --no-fund && npm run generate \
  && ML_FRONTEND_OUT_DIR="$ML_REACT_ASSET_DIR" npm run build)
uv run python scripts/run_test_tier.py browser

python3 scripts/qualify_exact_artifact.py \    # slow tier through the
  --source-sha "$(git rev-parse HEAD)" \       # installed wheel
  --test-tier slow

uv run python scripts/run_test_tier.py complete \   # every test tier at one
  --source-sha "$(git rev-parse HEAD)"              # clean exact head
```

The browser tier reads `ML_REACT_ASSET_DIR` with no fallback. Build the assets
outside the checkout, because a clean tree is itself a check.

`complete` verifies the source is that exact, clean SHA, runs `fast`, verifies
the browser payload, runs `browser`, then has the exact-artifact qualifier run
`required-installed` and `slow` against one built wheel. It does not run
`probe` or `shipped-sqlite`, which only `container-image` runs, nor ruff, mypy,
the frontend checks, the licence inventory, `npm audit` or the
reproducible-build comparison. Passing it does not qualify a head;
[what merging proves](../../CONTRIBUTING.md#what-merging-proves) lists every
check.

Exact-artifact qualification accepts any executable `uv` on `PATH`, including
a version-manager shim.

### The audio probe

In `slow` the probe file uses a host `ffprobe`, so a workstation running the
slow tier needs ffmpeg installed. In `probe` it uses the binary extracted from
the image `container-image` just built. A missing probe fails either cohort
with a diagnostic naming the paths it searched; it never skips. Every other
tier uses recorded probe documents.

### Parallel and sharded runs

`--workers N` (N at least 2) runs `fast` or `browser` in N fresh
interpreters, largest work unit first: a file for `fast`, a group of functions
from one file for `browser`. The result matches a serial run's. The tier is
refused if any unit refuses or leaves no record, or a split loses a collected
case. A test that passes only after another file has run is a defect.

`--shard K/N` (fast, with `--workers`) runs one share of the tier. Every shard
computes the same split from the per-file times in
`scripts/fast_tier_timings.json`, estimating unlisted files from their size. A
stale table only unbalances shards; it never drops or repeats a file. Refresh
it with `uv run python scripts/run_test_tier.py fast --workers 4
--record-timings`.

### Local Linux fast checks

For platform, permission, locking or runner changes, run the fast tier on Linux
before publication, or record why not and what covers it instead. From a clean
committed candidate, with Docker or OrbStack running:

```bash
python3 scripts/run_linux_fast.py \
  --source-sha "$(git rev-parse HEAD)" \
  --cache-directory "${TMPDIR:-/tmp}/mechanical-librarian-linux-cache"
```

The helper refuses a dirty tree or a mismatched SHA. It runs only `fast`, in a
disposable clone, as the host's non-root UID and GID, in an image built from
the Dockerfile's pinned toolchain. Only the clone and the cache are mounted,
so never choose a home, media, credential or production-data directory as the
cache. The cache and image stay for reruns; the container and clone are
removed on exit.

The helper gives each test a 180-second deadline (`--test-timeout-seconds`)
and the whole run 1,800 seconds (`--job-timeout-seconds`). Source drift during
the run invalidates it. It checks a snapshot of trusted source; it is not a
sandbox for untrusted code. Nor is it exact hosted parity: architecture, SQLite
build and resources differ.

`run_test_tier.py` has no per-test deadline unless given
`--test-timeout-seconds`. On expiry it writes one `test-tier-timeout-v1` record
to standard error and exits nonzero, even if the test catches
`BaseException`. It is a deadlock backstop, not a performance measurement.

Directory `fcntl.flock` behaves differently on macOS and Linux. The persistence
adapters use `fcntl` only on Linux, and Linux-only lock and permission tests
skip elsewhere, so a macOS run gives no Linux coverage of them.
`open_repository` takes a non-blocking exclusive lock and refuses a second
repository in the same parent directory. In tests, use separate directories or
close the first repository before opening another.

### Slow tier

A test may leave routine qualification only when its expensive property is
scale, endurance, exhaustive breadth or resource measurement, and a smaller
required test covers the same functional path and failure transitions. Slow
tests are never silently skipped.

`qualify_exact_artifact.py --test-tier slow` runs two commands in one freshly
built wheel environment: the other slow files, then
`test_owner_browser_responsiveness.py`. The two parts must together equal the
slow inventory, or qualification is refused. Each slow command has 1,800
seconds; setup, build, probes and `required-installed` have 825.
`--test-tier complete` runs `required-installed` first, then both slow
commands. The first failure or timeout stops the sequence, and evidence is
issued only when every command succeeds.

The largest slow fixtures are synthetic, run through the installed wheel, and
need Chromium installed and verified first. Their receipts hold timings (every
sample and p50/p95/max), counts, budgets and RSS, never routes, identifiers,
paths, SQL, credentials or request values.

## The gate

Every pull-request head runs `fast-source`, `fast-source-2` to `fast-source-4`,
`exact-installed`, `protected-browser`, `container-image` and
`dependency-audit`, and all are required. The workflow also runs on every push
to `main`, so two green pull requests cannot break `main` together unnoticed.
Each job checks out `github.event.pull_request.head.sha || github.sha`; a newer
head cancels only pull-request runs.

- **Fast tier.** `fast-source` runs the source checks then shard 1; the shard
  jobs run 2 to 4, each with `--workers 2 --shard K/4`. `fast-source` has 25
  minutes including setup.
- **Browser tier.** `protected-browser` runs serially; timing-bound browser
  tests assume one Chromium per runner. It caches the Chromium payload per
  Playwright version, saving it only after
  `scripts/verify_playwright_browser.py` accepts its layout and tree digest.
  It does not detect corruption of a payload within one Playwright version.
- **Container image.** `container-image` builds the shipped image at the same
  revision, so a Dockerfile regression fails the gate. It extracts the pinned
  `ffprobe` and SQLite for the `probe` and `shipped-sqlite` tiers and never
  starts or pushes the image.
- **Exact installed artifact.** `scripts/qualify_exact_artifact.py` proves that
  what merges is what the source says. It builds the wheel from a clean
  checkout at the given SHA, installs it without dependency resolution into a
  fresh environment seeded from the lockfile, and runs the tier through that
  interpreter outside the checkout, with the source-only workbench proved
  absent. Any mismatch in source, wheel, `RECORD`, dependencies, payload or
  import origin stops qualification. Its path-free JSON evidence binds source,
  wheel, payload, tier and output digests.

Passing CI is necessary but is not merge authority; see
[delivery and merging](slice-delivery.md).

## Runner invariants and diagnostics

pytest is the engine beneath the tiers, not a second entry point. Test
identities are the dotted, path-free names `unittest.TestCase.id()` produces,
so cohort digests stay stable; the runner honours `getTestCaseNames` and
`load_tests`, and refuses a case that is not path-free or collides with
another. It fails closed on any pytest environment variable that could change
what a cohort runs, and disables plugin autoload.

Accounting follows unittest: `AssertionError` is a failure, anything else an
error, and an unexpected success refuses the tier. A tier passes only when
pytest's exit code and the runner's accounting agree.

A failure is diagnosable from the diagnostic stream alone. A failing tier
reports every failed identifier and each traceback, within byte bounds that
`scripts/run_test_tier.py` defines; truncation says what it dropped. The
exact-artifact qualifier keeps only a bounded tail of a child tier's streams,
so the full list is on the runner's standard output. No path or value reaches
an evidence record: a failing command's output appears only in a redacted
narrative on standard output, and the `test-tier-result-v1`,
`test-tier-stall-v1` and `test-tier-timeout-v1` lines have standard error to
themselves.

## Coverage and test value

No coverage percentage is enforced. The decision is revisited before 1.0. A
threshold measures lines executed, not behaviour proven. A change that adds a
code path adds the tests that prove it, and review enforces that.
Security behaviour is proven in the unit and integration cohorts; there is no
separate security, contract or documentation cohort.

Coverage and mutation testing are optional analysis, run when someone wants
evidence about a module. `mutants/` is generated, ignored by git and never
committed.

```bash
uv run python -m coverage run --branch \
  --source=src/mechanical_librarian scripts/run_test_tier.py fast
uv run python -m coverage report --precision=1 --sort=cover

export PYTHONPATH="$PWD/mutants/src"            # the mutated package, not
uv run python scripts/generate_mutants.py       # the editable install
uv run python scripts/run_mutation_audit.py \   # pass 1: capped
  --only application.review_web --max-tests 40 --out pass1.jsonl
uv run python scripts/run_mutation_audit.py \   # pass 2: the unkilled set
  --only application.review_web --max-tests 0 \
  --survivors-from pass1.jsonl --out pass2.jsonl
```

`[tool.mutmut]` in `pyproject.toml` names the modules under audit. Without
`PYTHONPATH` every mutant appears to survive. The audit refuses an empty map or
a stale test snapshot, and appends one JSON line per mutant, so a rerun skips
recorded mutants. A capped `killed` verdict is sound; a capped
`survived` is not, so only pass 2's survivors are unkilled. `first_failure`
names one killer, not all; to prove a test kills nothing, run it alone with
`scripts/mutation_kills_for_test.py`.

A non-Linux coverage run leaves Linux-only branches unexecuted. Tests that read
source as text cannot kill mutants, and code reached only through a subprocess,
the console script or a container shows as "reached by no test". Survivors in
`help=`, `metavar=` or `description=` strings, and in error messages no seam
exposes, need no test.

## Test economy

These rules apply to every new test, beside the code economy rules in
[AGENTS.md](../../AGENTS.md).

- **Parameterise variants** with `@pytest.mark.parametrize` or vitest's
  `it.each`, one reported case per variant, never a loop. On a
  `unittest.TestCase`, convert or use `subTest`. Keep a variant separate only
  when it needs its own name, and say why.
- **Build fixtures through shared builders**: `tests/support/` for the
  backend, `frontend/tests/fixtures.tsx` for the frontend. A test module never
  imports another test module's fixtures. Inline a payload only where its
  exact shape is under test.
- **Force failures through a seam that survives refactoring.** Do not replace a
  method on the object under test and call a sibling that uses it: if the
  callee moves, the test stays green while testing nothing. Use a fake
  collaborator, an injected port or a connection substituted at construction.
  Where a private member really is the contract, say so at the site.
- **Drive frontend timers with fake timers.** Test debounces, dwells and
  intervals with `vi.useFakeTimers()`, `fireEvent` and
  `vi.waitFor(until, { interval: 0 })`. Under fake timers `userEvent` and
  `findBy*` hang instead of failing, even with `advanceTimers`. Keep vitest
  globals off and never shim `globalThis.jest`, which would make each
  `waitFor` poll advance the clock 50 ms. Pin each advance to an effect the
  code after the timer owns: absent one millisecond early, present on time. If
  that effect does not appear in the scenario, prove the interval in a focused
  test; do not weaken the assertion. A timer-count delta is not proof. Use
  real timers only where no timer is measured.

## Fixtures, hostile input and privacy

[CONTRIBUTING](../../CONTRIBUTING.md#fixtures-and-privacy) sets what fixtures
may contain. In addition:

- Hostile files, network responses, configuration, credentials, SQLite rows
  and subprocess output are bounded and fail closed. Diagnostics carry no
  values when rejected input may hold secrets, private paths or media text.
- Synthetic EPUB members have fixed timestamps, so probe identities do not
  depend on the clock.
- A hostile-input test names a supported external actor and a reachable
  boundary. Typed in-process collaborators are not attacker-controlled, unless
  the product adds an untrusted extension seam.
- No live library, provider or production service is used in development or
  CI. Public evidence from any qualification is aggregate only. Case
  identities, metadata, paths, media, private corpus rows and credentials
  never leave the private side.
- Captured [provider evidence replay](provider-evidence-replay.md) is private
  maintainer qualification, never development or CI. Each live capture needs
  the owner's explicit authority and an owner-selected cohort. It does not
  replace installed, scale, browser or semantic qualification.

## Qualification against a real service

`scripts/qualify_audiobookshelf_field_effect.py` qualifies writes against each
pinned Audiobookshelf image, using only synthetic disposable libraries and API
keys and an in-process provider fake. It never contacts a production provider
or library, and writes to Audiobookshelf only through its API, never directly
to its database or through its web UI.

```bash
uv run --locked python scripts/qualify_audiobookshelf_field_effect.py \
  --source-sha "$(git rev-parse HEAD)"
```

Run it from a clean exact head; a dirty tree or a head other than
`--source-sha` fails `source_head_mismatch`. It archives that head, builds and
installs its wheel, and qualifies the installed artifact. Without `--version`
it qualifies every pinned image, and `--version` selects one and can be
repeated. It reads Audiobookshelf's latest release from the GitHub API and
fails `latest_stable_not_pinned` when that release is not pinned, so a new
upstream release needs a new pin before the canary passes again.

It creates scoped disposable API-key users, deletes their keys on teardown,
and removes only its own labelled container. Per image it proves one metadata
and tags PATCH and one digest-verified cover upload, with read-back, refusal
and ambiguous-response reconciliation. Besides its EPUB libraries, a separate
disposable library holds one generated audiobook for the audiobook
destinations. A destination whose shape the service rejects is recorded as
`field_unavailable`, and the other destinations still run. An unreachable
service, a key without write authority, or a write the service accepted but
did not make fails the run instead. Every run also drives the installed
application against two disposable libraries through preparation, owner
decisions, consent, continuous scheduling, pause and resume, rescan,
external-drift regeneration and restart recovery.

`--corpus` replaces those libraries' two generated EPUBs with a private
operator batch: a directory outside the repository holding 6 to 8 `*.epub`
files, or the run fails `operator_corpus_invalid`. The aggregate
`audiobookshelf-field-effect-v3` receipt holds the source SHA, image digests,
field outcomes, the installed lifecycle summary, the outcome for each of seven
synthetic descriptions and, with a batch, `operator_corpus: true` and each
image's `operator_cases`. It never holds credentials, tokens, source metadata
or image bytes.

The container lifecycle (`scripts/qualify_container_lifecycle.py`) and the
[recoverability envelope](recoverability-qualification.md) have their own
maintainer-manual qualifiers.
