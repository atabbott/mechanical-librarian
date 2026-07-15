---
name: deliver-functional-slice
description: Deliver a Ready Mechanical Librarian functional slice as one demonstrable vertical outcome with bounded exploration, sole-writer implementation, acceptance and quality review, risk-based safety review, qualification, and pull-request evidence.
---

# Deliver a functional slice

Read `.codex/operating-policy.md`. Start only from a Ready slice tracker with an
observable end-to-end outcome and complete exit criteria. Freeze its base,
dependencies, included tickets, exclusions, and demonstration.

## Explore the seam

Use one or more bounded `reader` tasks only where they can independently inspect
contracts, persistence, adapters, interfaces, or external constraints. Combine
their evidence into one vertical implementation plan; do not create horizontal
layer PRs.

Choose Tier 2 only for an actual risk trigger, not because the slice is large.

## Implement the outcome

Assign one `worker` as sole writer. Keep contracts, adapters, persistence,
interfaces, migrations, tests, and documentation required for the demonstration
together. Do not claim constituent tickets whose full criteria are unmet.

Run focused checks as seams settle. From the frozen final state, run the full
relevant gate and the supported end-to-end demonstration.

## Review

Read `.codex/review-method.md` and create a frozen Git packet.

- Run slice-acceptance review against every exit criterion and demonstration.
- Run code-quality review against the whole diff.
- Run safety review and a challenger only for Tier 2.

Use one initial wave and one targeted re-review per accepted finding family.
Do not reopen unaffected reviews or extend the slice with optional improvements.

## Deliver

Record final ref, checks, demonstration, review digests, findings, and included
tickets. Commit focused boundaries on the slice branch, push normally, and open
or update the pull request with accurate closing references.

Read `.codex/codex-connector-review.md` after the PR opens. Observe automatic C1
without posting a duplicate request. Do not merge without explicit authority.
