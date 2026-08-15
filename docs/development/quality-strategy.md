# Quality strategy

Quality means the supported outcome is understandable, bounded, secure,
recoverable within its documented envelope, and backed by exact evidence.

Every pull-request candidate runs independent required `fast-source`,
`exact-installed`, and `protected-browser` jobs. The three jobs are deliberately
not recombined because the former serial qualification repeatedly timed out.
There is no runner-only aggregate job: the trusted delivery controller requires
all three exact-head results. The same workflow also runs on push to `main`, so
every merged commit is verified and two individually green candidates cannot
break `main` together in silence. Each job checks out
`github.event.pull_request.head.sha || github.sha`, so the exact-head semantics
hold on both events; the concurrency group is the pull request on a pull-request
event and the merged commit on a push, and only pull-request work is cancelled
by a superseding head. A fourth `container-image` job builds the shipped image
with `docker build` at that same revision, so a Dockerfile regression fails the
gate. It never runs, loads, or pushes the image: exercising the built image's
lifecycle remains a maintainer-manual step. The controller's required-check set
remains the three exact-head qualification jobs. The `protected-browser` job
restores the pinned Chromium payload from a cache keyed on the installed
Playwright version and saves it only after
`scripts/verify_playwright_browser.py` accepts it, so an inexact payload is
never persisted and every run still verifies the payload it uses. The
exact-artifact
qualifier validates a newly built wheel before installation, seeds a new
environment from all locked dependency groups, installs the explicit wheel
without dependency resolution, validates archive and installed `RECORD`
integrity, and compares the wheel and installed distribution payloads byte for
byte. Its isolated interpreter runs a representative smoke cohort before the
exact closed installed command help is exercised outside the checkout and the
source-only workbench is proved absent. Scale, endurance, exhaustive,
and resource proofs remain in the explicit exact-head slow tier. Aggregate
evidence binds the source, wheel, payload, selected tier, and command-output
digests. A test that makes no progress for 30 seconds emits one bounded,
path-free test identifier so a hosted timeout remains diagnosable without raw
test output. A failing tier reports every failed identifier — the list is not
truncated — and each failing test's formatted traceback, bounded to the last
4 KiB per test and 256 KiB across the whole stream, framing included, so a
broad failure stays readable and stays inside the exact-artifact qualifier's
per-stream capture bound. A truncated report always declares how many
tracebacks it omitted. Raw
cohort output remains discarded; only the traceback of a test that already
failed is reported, and it carries the runner's own repository paths. That
narrative stream is standard output, so the machine-readable
`test-tier-result-v1` and `test-tier-stall-v1` lines keep standard error to
themselves. Changes add focused unit/integration/security tests, documentation,
changelog, schema or ADR updates as applicable. Tests, types, safety gates, and
failure handling are never weakened to make a change pass. The authoritative
[testing strategy](testing-strategy.md) owns the commands and tier boundary.

Hostile files, network responses, configuration, credentials, SQLite rows, and
subprocess output are bounded and fail closed. Diagnostics are value-free when
rejected input may contain secrets, private paths, or media text. Synthetic
fixtures are repository-authored and contain no personal or copyrighted
material.

Qualified advisory-cover preparation proves the composed F22 path: one
qualified observer dispatch occurs before projection and package cutover, and
the immutable consideration is replayed without a second dispatch or
field-effect authority. An unavailable profile, reviewer refusal or failure,
malformed reviewer output, persistence failure, and staged-currentness drift
all stop safely before package cutover; a case without a cover proposal skips
advice normally. Existing cover-consideration and browser coverage proves
stale retirement, owner-only manual retry and history, and that advice never
creates automatic cover authority.

Policy-draft qualification additionally proves the authenticated private
state/validate/create/read/preview schemas, CSRF and Origin/Host failure paths,
closed bounded maps, and declared or chunked bodies above 16 KiB. Browser proof
loads the policy-only script at 1440 and 390 CSS pixels, including active-draft
initialization, disabled automatic and unsupported Explicit choices, chapter
focus/navigation, hostile-safe DOM rendering, and absence of console errors.

Policy-activation qualification proves exact draft and per-library resolution
revalidation, an atomic old/new authority boundary, both human-commit orderings,
stale evaluation, historical Ignore replay across detail/browse/shadow,
Ask/Ignore behavior, exact-current cohort selection, response-loss replay,
consecutive-activation gates, fixed denominators when new work arrives, reuse
of eligible evidence, restart/idempotency, and durable typed extraction/provider
failure, safe stage-specific retry, and acknowledgement. Declared and chunked oversized activation bodies
fail before parsing. Browser proof directly inspects the selected B
notebook, active-versus-draft wording, compact shared activity, sticky desktop
chapters, pinned compact mobile selection, keyboard focus, responsive layout,
and absence of Automatic, apply, target-write, or media-write authority.
It also proves bounded processing refresh, polling restart after retry, and
terminal stop.

Provider retry changes require deterministic proof of the fixed policy:
accepted and rejected typed transport classes, status and canonical
`Retry-After` handling, exact per-request and per-observation attempt limits,
immediate aggregate-byte charging, pre/post-delay and pre-dispatch deadline
gates, cumulative pacing, cancellation propagation, and diagnostic redaction.
Tests also bind foreground-batch maxima and the exact installed command path.
Where owner credentials are explicitly authorized, one owner-private canary
may confirm that installed-path composition works without retaining a key,
query, raw response, private path, or report.

Functional slices are reviewed and qualified at the exact candidate head.
Tier-2 work adds independent safety and challenger review plus receipt-bound
aggregate qualification. Public CI is necessary but not merge authority; the
maintainer-owned exact-head controller and reconciliation workflow remain
authoritative.

A change is done when its supported outcome and failures are documented and
tested, generated artifacts reproduce, gates pass at the exact head, required
reviews converge, tracking is correct, and no required work is hidden in an
untracked artifact.
