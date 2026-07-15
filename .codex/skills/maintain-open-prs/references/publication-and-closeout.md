# PR publication, external mutation, and closeout

Read this reference before any commit, push, comment, thread mutation,
connector trigger, Project change, or merged-work closeout. Root alone performs
these actions under `.codex/standing-authority.md`; record each as a v3 mutation
with before/after state, external ID, result, and readback.

## Qualification and safe push

Run targeted checks after each coherent remediation and complete current gates,
linked demonstrations, and realistic negative/failure checks from the final
clean state. Investigate flakes instead of rerunning until green. Run:

```text
python3 .codex/scripts/review_preflight.py --packet <frozen-packet.json>
```

The immutable packet binds repository common directory/origin, worktree/branch,
base/head commits and trees, allowed/required paths, documentation, migration,
security, changelog, private/Linux qualifications, and required gates. Every
declared gate is mandatory; there are no bypass flags.

Immediately before a publishable commit/push:

1. confirm every accepted family and same-thread continuation or qualified
   unavailable-thread replacement is complete;
2. confirm exact-identity private/Linux/external/local evidence is current;
3. fetch and require remote head equals the frozen/reconciled SHA;
4. stop and reassess if it moved;
5. stage only bounded remediation;
6. create neutral focused commits at review/rollback boundaries (one commit is
   valid for a genuinely inseparable small packet); and
7. push normally to the existing head branch.

Never force-push without explicit case-specific approval, push to `main`, or
overwrite another contributor. A `NOT_READY` checkpoint may be pushed only
when its bounded repaired families independently qualify and direct evidence
proves the remaining blocker existed before and was neither introduced nor
worsened. Record the blocker and request no connector review.

## Post-push and connector protocol

After a push, refresh checks, threads, reviews, description reactions, and
connector state against opening/current heads. Automatic `C1` begins on the
opening head as `PENDING_PICKUP`. `eyes` from the connector means
`IN_PROGRESS`; removal plus `+1` means `CLEAN`; a posted review means
`FINDINGS_POSTED`. Observe pickup for two minutes. If absent, record
`PICKUP_UNOBSERVED` and revisit later without a duplicate trigger. Once picked
up, review may take more than ten minutes.

Apply `.codex/codex-connector-review.md` exactly. `C2` is the one authorized
manual follow-up after all accepted `C1` and remediation-discovered findings
are closed, affected independent continuations and gates pass, and fixes are
pushed. Never request `C3`. Finish all other mutations before the terminal C2
trigger and perform no later mutation in that invocation.

Connector advice is not automatically binding unless branch protection says
so. Evaluate it through the normal family gates. A pending/stale optional
connector does not invalidate current local qualification, but always blocks a
claim that the connector round completed.

## Comments and review threads

Reply to every declined/deferred substantive comment with its evidence-backed
disposition and whether a confirmed follow-up exists. Never promise an
uncreated ticket. An unresolved thread blocks only when branch protection
requires it or its underlying material in-scope finding remains open.

After qualified remediation, reply on addressed threads with the current SHA,
change/disproof, and verification. Resolve only when remediated, disproved by
direct evidence, already satisfied, or obsolete. Keep questions, ambiguity,
partial fixes, and unresolved disagreement open. Re-read and record the thread
after mutation.

Do not submit reviews, approve team work, mark drafts ready, or merge without
separate authority. While external state is pending, assess another independent
cohort; do not poll indefinitely during an ordinary invocation.

## Deferred findings

After qualification, triage only concrete non-blockers without delaying the
PR. Discard preferences/theoretical hardening, search duplicates, and create a
focused defect only for material evidence-backed value. Reply on the source
thread with the confirmed ticket/duplicate or reason no follow-up was created.

## Merged-work closeout

When a maintained PR merges or the user asks to finish it:

1. Verify `MERGED`, merge commit/actor, reviewed head ancestry, and checks at
   merge. Closed alone is not merged.
2. Resolve recognized closing metadata to exact tracker/tickets. Verify closure
   and Project `Done`; correct stale Project status when evidence supports it,
   but do not directly close a ticket whose closing metadata failed.
3. Use the final acceptance matrix. In fresh context, reconstruct criterion-
   level evidence first. Check only canonical acceptance/exit criteria that are
   PASS and actually within the merged closure. Preserve wording/order; leave
   partial, failing, undemonstrated, unjustified N/A, future, dependency, and
   outside-boundary items unchecked and report inconsistencies. On parents,
   check only fully satisfied aggregate criteria and completed constituents.
4. Recompute downstream availability from native blockers. Preserve closed
   dependency history. Keep items Blocked with exact open blockers; otherwise
   move to Ready only when the full readiness contract/fields pass, else
   Triaged with exact gaps. Never start newly unblocked work during closeout.
5. Reconcile every affected Project field needed by authoritative dependency,
   classification, slice, package, release, priority, risk, size, documentation,
   or delivery evidence without inventing a new decision.
6. For merged slices, verify `main` can fast-forward, the isolated worktree is
   clean, and the source branch is fully contained. Remove only that worktree
   and merged local/remote branch; preserve unrelated state.
7. Re-read issue bodies, dependency summaries, Project items, PR/branch state,
   and primary worktree. Record every checklist, transition, mutation readback,
   cleanup action, and automation discrepancy.

## Queue report

Report each PR as `UPDATED — AWAITING CHECKS`, `UPDATED — OPTIONAL REVIEW
PENDING`, `CLEAN — APPROVED FOR MAINTAINER DECISION`, `NO ACTION`, `BLOCKED`,
`EXCLUDED`, or `MERGED — CLOSED OUT`. Include frozen/final SHAs, findings,
changes/commits, checks, external state, exact next action, cohort dependencies,
deferred dispositions, `PRn` separately from `I` and `C1/C2`, and final Project
state/mutations. A merged closeout also reports merge commit, closed tickets,
checklists, downstream transitions, retained blockers, and cleanup.

Never call a PR ready while checks fail, acceptance is unproven, or accepted
material findings remain. Distinguish fresh local evidence, observed remote CI,
inherited evidence, explicit N/A, and unavailable gates without claiming parity.
