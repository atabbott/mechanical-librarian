# Worker scope and verification control

Before any implementation or remediation edit:

1. record the target/ref, branch and `git status --short` baseline;
2. declare permitted path prefixes from the assigned packet and preserve every
   pre-existing path as user-owned unless explicitly in scope;
3. pass existing formatters/linters an explicit boundary-derived file list, or
   record a justified `NOT_APPLICABLE` skip; and
4. confirm whether `.codex/model-selection.md` requires a pre-edit
   `ROOT_ESCALATION` instead of starting the normal worker.

After every formatter, linter, generator, and coherent edit cohort, compare
status with the baseline. Unexpected paths stop work before staging or further
mutation; report drift without reverting user-owned changes. Run the cheapest
decisive targeted checks, report failures as observed, and investigate likely
flakes rather than rerunning until green. Root owns final complete gates and
evidence invalidation decisions.

For pre-edit high-risk routing, record `ROOT_ESCALATION` and let root open the
first writer interval; never create a dummy worker event. For a trigger found
after work begins, stop the worker before handoff. Record frozen tree/ref,
packet digest, evidence, invariant, transferred files/behavior, prohibited
scope, and expected checks. Root and worker never edit concurrently.

Workers do not commit, stage, push, open/update pull requests, reply/resolve
threads, trigger connectors, or mutate issues/Project/external state unless root
explicitly assigns that operation within standing authority. Reviewers and
analysts never receive such authority.
