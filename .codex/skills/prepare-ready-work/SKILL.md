---
name: prepare-ready-work
description: Turn rough Mechanical Librarian ideas, issues, trackers, and deferred findings into coherent vertical deliverables and implementable Ready tickets through classification, slicing, criteria, dependencies, risks, demonstrations, and independent readiness review.
---

# Prepare Ready work

Read `.codex/operating-policy.md`. This is a read-only planning workflow unless
the user authorizes issue, relationship, or Project mutations. Do not implement
the proposed work.

## Freeze and analyze intake

Identify the source idea or problem, affected users, observable outcome,
constraints, existing evidence, and unresolved decisions. Use bounded `reader`
tasks for repository analysis, duplicate search, dependency evaluation, or
external research.

Classify the delivery as functional slice, focused change, defect, maintenance,
documentation, dependency work, deferred idea, or rejection. For non-trivial
dependencies, read `references/dependency-evaluation.md`.

## Shape the deliverable

Prefer vertical outcomes over layer or work-package tickets. Define:

- user-visible or operator-visible outcome;
- included and excluded behaviour;
- executable acceptance criteria and demonstration;
- dependencies and ordering;
- tests, docs, migration, safety, and compatibility obligations; and
- risks that genuinely affect implementation.

Use `references/readiness-packet.md` when producing a formal ticket set.

## Review and publish

Read `.codex/review-method.md`. Run one fresh readiness review; add safety review
or a challenger only for Tier 2 planning boundaries. Apply one correction cohort
and one targeted re-review, then stop.

Present the proposed set and mutations to the user. After approval, create or
update tracking atomically, verify relationships and Project fields, and report
the exact resulting IDs and state. Workflow-only `.codex` maintenance does not
require a ticket.
