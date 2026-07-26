# ADR-0015: Isolate qualification-only Codex shadow review

- Status: accepted
- Date: 2026-07-26

## Decision

The first AI reviewer is one exact maintainer-provisioned Codex CLI artifact,
authenticated only through a ChatGPT subscription, and invoked only through a
Linux Bubblewrap boundary. The frozen tuple is `gpt-5.6-sol` with `medium`
reasoning, `shadow-review-prompt-v1`, `codex-shadow-profile-v1`,
`shadow-review-packet-v1`, `shadow-review-result-v1`, and
`qualification-only-policy-v1`. The declared executable and sandbox identities,
versions, limits, and tuple are bound into every request. Paths remain local.
Any drift fails closed; runtime installation, upgrade, fallback, API-key
authentication, prompt choice, model choice, or another backend is unsupported.

The sanitized packet is assembled from one exact current ReviewCase, its
ProposalSet, and referenced report evidence. It excludes the target connection,
library, item ID, paths, credentials, service clients, and provider payloads.
The direct closed result must cover every actionable proposal once and may only
recommend an exact packet candidate, keep the current value, or manual review.
It is advice and has no decision, apply, or write authority.

Request, attempt, dispatch, and result or failure facts are separate append-only
SQLite records. The first attempt exists before process start. There is no
automatic retry. After a dispatched failure, the durable projection is
`unknown`; only the exact reevaluation command may append an attempt, and only
after the supervised process group is proved absent. Reevaluation binds that
proof to the exact latest attempt ID and claims its successor in one immediate
transaction; a concurrent or changed latest attempt loses the compare-and-swap.
Every attempt also persists its claimant PID and Linux start token. An
undispatched attempt is safe to reevaluate only after that exact claimant is
absent, because the start gate makes dispatch persistence the durable release
condition.

All authentication and reviewer processes share one exclusive owner-only auth
store lock. Review execution ignores user configuration and rules, disables
shell, execution, web search, apps, agents, hooks, memories, plugins, updates,
feedback, analytics, history, and approval, uses an empty private job
directory, bounded resources and output, and discards raw streams, reasoning,
and malformed output.

The runner opens and verifies the declared Codex and Bubblewrap artifacts once,
then invokes their bound descriptors so path replacement cannot change the
verified executable. A minimal controller enters its own process group and
waits on a one-byte gate. The application durably records that controller's
actual PID, process group, and Linux start token before releasing it to exec
Bubblewrap; callback failure or parent death closes the gate and exits without
reviewer execution or egress. A dispatch with no terminal outcome is therefore
still conservatively `unknown`.

## Consequences

This decision intentionally duplicates a small direct output validator at the
CLI trust boundary and the domain validator at persistence. The duplication
prevents a malformed adapter or stored row from becoming accepted advice.

The development runtime does not bundle Codex CLI or Bubblewrap. Qualification
uses exact owner-provisioned artifacts after licence and provenance review;
F15a must include the qualified binary and sandbox in its reproducible licence
inventory and SBOM before they become a supported container dependency.

ADR-0013 continues to require absent or exact-current state. The new tables are
not migrated, imported, converted, repaired, or selectively purged.
