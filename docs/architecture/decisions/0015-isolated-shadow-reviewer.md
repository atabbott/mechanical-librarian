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
after the supervised process group is proved absent.

All authentication and reviewer processes share one exclusive owner-only auth
store lock. Review execution ignores user configuration and rules, disables
shell, execution, web search, apps, agents, hooks, memories, plugins, updates,
feedback, analytics, history, and approval, uses an empty private job
directory, bounded resources and output, and discards raw streams, reasoning,
and malformed output.

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

