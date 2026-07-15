# Adaptive concurrency

Record the runtime slot limit before spawning and reserve one slot for root.
Reader capacity is `min(max(0, runtime_slots - 1), 3,
workflow.max_parallel_readers)` using `.codex/team.toml`. The configured
`agents.max_threads` is only a ceiling.

For queues, form ephemeral cohorts around independently frozen targets. A
target is independent only when it has no stacked-base or delivery dependency,
shared branch/worktree, active foreign writer, or scope overlap requiring a
shared decision. Root retains queue selection, synthesis, mutation, and
completion authority.

## Scheduling invariants

- Never run more than one writer. Treat capacity as one queue-wide lease and
  release it before another target's writer starts.
- Do not run a writer concurrently with any agent that may edit its worktree.
- Create every initial reviewer for every target/ref as a new
  `fork_turns="none"` thread. Never reuse a prior target's reviewer thread for
  an initial review.
- Reuse a reviewer thread only for targeted continuation of that review's own
  finding. `REPLACEMENT_ISOLATED` uses a new thread only after the original is
  proven unavailable. A fresh publication challenger always receives a new
  isolated thread.
- If all applicable reviewers do not fit, schedule multiple read-only waves
  against the same frozen ref. The review barrier stays open until all complete;
  never omit a reviewer or begin partial synthesis/remediation.
- While one target awaits external checks, use spare readers on another truly
  independent target, but never create overlapping writers.
- If no subagent slot is available, root may perform discovery but cannot
  impersonate an independent reviewer.

Read-only analyst/research tasks may be parallel only when each has one bounded,
non-overlapping question, the raw minimum context, and no delegated synthesis
or external mutation authority. `prepare-ready-work` uses at most two analyst
tasks; idea exploration uses at most three researchers.
