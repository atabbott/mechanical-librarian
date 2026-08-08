# Quality strategy

Quality means the supported outcome is understandable, bounded, secure,
recoverable within its documented envelope, and backed by exact evidence.

Every pull request runs independent required fast-source, exact-installed, and
protected-browser jobs plus a stable aggregate gate. The exact-artifact
qualifier validates a newly built wheel before installation, seeds a new
environment from all locked dependency groups, installs the explicit wheel
without dependency resolution, validates archive and installed `RECORD`
integrity, and compares the wheel and installed distribution payloads byte for
byte. Its isolated interpreter runs a representative smoke cohort before the
installed CLI is exercised outside the checkout. Scale, endurance, exhaustive,
and resource proofs remain in the explicit exact-head slow tier. Aggregate
evidence binds the source, wheel, payload, selected tier, and command-output
digests. A test that makes no progress for 30 seconds emits one bounded,
path-free test identifier so a hosted timeout remains diagnosable without raw
test output. Changes add focused unit/integration/security tests, documentation,
changelog, schema or ADR updates as applicable. Tests, types, safety gates, and
failure handling are never weakened to make a change pass. The authoritative
[testing strategy](testing-strategy.md) owns the commands and tier boundary.

Hostile files, network responses, configuration, credentials, SQLite rows, and
subprocess output are bounded and fail closed. Diagnostics are value-free when
rejected input may contain secrets, private paths, or media text. Synthetic
fixtures are repository-authored and contain no personal or copyrighted
material.

Policy-draft qualification additionally proves the authenticated private
state/validate/create/read/preview schemas, CSRF and Origin/Host failure paths,
closed bounded maps, and declared or chunked bodies above 16 KiB. Browser proof
loads the policy-only script at 1440 and 390 CSS pixels, including active-draft
initialization, disabled automatic and unsupported Explicit choices, chapter
focus/navigation, hostile-safe DOM rendering, and absence of console errors.

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
