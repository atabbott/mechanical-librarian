# Quality strategy

Quality means the supported outcome is understandable, bounded, secure,
recoverable within its documented envelope, and backed by exact evidence.

Every pull request runs a lockfile check, non-editable pinned install, full test
suite, architecture checks, distribution build, and installed CLI smoke.
Changes add focused unit/integration/security tests, documentation, changelog,
schema or ADR updates as applicable. Tests, types, safety gates, and failure
handling are never weakened to make a change pass.

Hostile files, network responses, configuration, credentials, SQLite rows, and
subprocess output are bounded and fail closed. Diagnostics are value-free when
rejected input may contain secrets, private paths, or media text. Synthetic
fixtures are repository-authored and contain no personal or copyrighted
material.

Functional slices are reviewed and qualified at the exact candidate head.
Tier-2 work adds independent safety and challenger review plus receipt-bound
aggregate qualification. Public CI is necessary but not merge authority; the
maintainer-owned exact-head controller and reconciliation workflow remain
authoritative.

A change is done when its supported outcome and failures are documented and
tested, generated artifacts reproduce, gates pass at the exact head, required
reviews converge, tracking is correct, and no required work is hidden in an
untracked artifact.
