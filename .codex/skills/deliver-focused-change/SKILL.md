---
name: deliver-focused-change
description: Deliver one bounded Mechanical Librarian non-slice change through classification, implementation, proportionate review, qualification, push, and pull-request creation. Use for fixes, documentation, tooling, dependencies, maintenance, audits, and genuinely standalone additions.
---

# Deliver a focused change

Read `.codex/operating-policy.md`. Freeze the requested outcome, exclusions,
base ref, and acceptance evidence before editing.

Repository workflow-only changes under `.codex/**` do not require a tracking
ticket. Other material changes follow normal repository tracking rules.

## Classify and inspect

Choose Tier 1 unless a Tier 2 risk trigger applies. Use a bounded `reader` task
only when discovery, external research, or reproduction can materially reduce
implementation uncertainty. Do not delegate work the root can answer directly
from the supplied evidence.

## Implement

Assign one `worker` as sole writer, or implement directly when delegation adds
more coordination than value. Constrain the task by outcome and prohibited
scope rather than a speculative file list.

Implement the smallest complete change. Add proportionate tests and docs, and
surface unexpected failures rather than adding future-proofing. Run cheap
targeted checks during work and the complete relevant gate once from the frozen
final state.

## Review

Read `.codex/review-method.md`. Create a Git-bound packet with
`.codex/scripts/review_packet.py create`.

- Require acceptance review for changed behaviour or governance.
- Require code-quality review for executable code or complex tooling.
- Add safety review and a challenger only for Tier 2.

Use one fresh review wave, one remediation cohort, and one targeted re-review
per accepted family. Stop on a remaining blocker; defer unrelated improvements.

## Qualify and deliver

Confirm the final ref, required checks, required reviews, resolved findings,
docs, and delivery evidence. Use a minimal run record when the work crosses a
session, waits externally, or needs comparison evidence.

Commit with a purpose-based message, push normally, and open or update the pull
request. If a PR opens, read `.codex/codex-connector-review.md`; observe automatic
C1 pickup without posting a duplicate request. Do not merge without explicit
authority.
