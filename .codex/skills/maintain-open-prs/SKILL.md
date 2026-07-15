---
name: maintain-open-prs
description: Maintain Mechanical Librarian pull requests through repository-wide inventory, thread-aware feedback assessment, bounded remediation, current qualification, connector reconciliation, and evidence-backed handoff or closeout.
---

# Maintain open pull requests

Read `.codex/operating-policy.md`. Inventory open PRs, their heads, checks,
reviews, unresolved threads, tracking state, and connector signals before
choosing work. Never assume the current checkout represents every PR.

## Assess without editing

Use bounded `reader` or fresh `reviewer` tasks to classify feedback as actionable,
already satisfied, obsolete, question, or optional. Read
`references/pr-review-evidence.md` when thread-level evidence or a multi-PR
handoff is needed.

Prioritize required checks and material accepted findings. Do not treat an
unresolved thread as actionable merely because it remains open.

## Remediate one PR

Freeze one PR head and one accepted finding cohort. Assign one `worker` as sole
writer. Make the smallest coherent correction, run targeted checks, then the
complete invalidated gate once.

Read `.codex/review-method.md`. Re-review accepted families in their originating
thread once. Add whole-diff or safety review only when the changed boundary
requires it. Stop on a remaining blocker; do not start an unbounded cleanup.

## Reconcile and close out

Push normally, then refresh head, checks, reviews, threads, and tracking. Reply
with concrete evidence and resolve only threads whose substance is fixed,
disproved, satisfied, or obsolete.

Read `.codex/codex-connector-review.md` for C1/C2. Never duplicate an automatic
request or wait beyond the bounded pickup window during an ordinary invocation.

Return each PR as ready, awaiting external state, blocked, or closed. Merge,
approve, close, or mark a draft ready only with explicit authority.
