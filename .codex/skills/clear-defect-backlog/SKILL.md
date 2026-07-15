---
name: clear-defect-backlog
description: Clear a supplied Mechanical Librarian defect list through bounded triage, safe reproduction, one-ticket-at-a-time implementation, proportionate independent review, qualification, and delivery. Use when Codex is asked to burn down or work through multiple bug tickets.
---

# Clear a defect backlog

Read `.codex/operating-policy.md`. Use Tier 1 per ordinary defect and Tier 2
only when its risk triggers apply.

## Triage the supplied list

Freeze the ticket list and repository base. Use `reader` tasks in parallel to:

- confirm each report is actionable and not already resolved;
- identify duplicates and dependencies;
- reproduce safely with synthetic or isolated data; and
- order tickets by blocking relationship, severity, then bounded effort.

Do not edit during triage. Keep uncertain reproduction separate from confirmed
product failure. Use `references/defect-evidence.md` only when building a formal
reproduction packet.

## Deliver one ticket at a time

For the next unblocked ticket:

1. freeze its acceptance criteria and likely paths;
2. assign one `worker` as sole writer;
3. implement the smallest complete correction and focused regression tests;
4. run targeted checks, then the current repository gate when warranted; and
5. stop the writer before review.

Do not combine independent defects merely to reduce PR count. Do combine true
duplicates or manifestations of one root cause when one bounded fix satisfies
their complete criteria.

## Review and qualify

Read `.codex/review-method.md`. Run a fresh reviewer for ticket acceptance and
code quality; one invocation may cover both for a small fix. Add independent
safety review and a challenger only for Tier 2.

Apply at most one remediation cohort and one targeted re-review per accepted
finding family. A remaining blocker stops that ticket without stalling triage
of unrelated tickets.

Record final ref, checks, review packet digest, findings, and delivery in the
minimal run record when the work crosses a session or needs future comparison.

## Deliver

Commit and push only the qualified ticket boundary. Open or update its pull
request with accurate evidence and required tracking. Never merge without
explicit authority. If the queue cannot continue, return a concise handoff
listing completed, blocked, duplicate, and remaining tickets.
