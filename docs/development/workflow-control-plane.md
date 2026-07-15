# Delivery workflows

The tracked `.codex` tree defines six repository workflows using four authority
profiles. A workflow names the profiles it may use and review tasks it normally
needs; it does not create a separate role for every specialty.

## Proportional control

- Tier 0 is read-only work and normally needs no durable record or review.
- Tier 1 is one bounded repository change with current checks and the
  workflow's review tasks.
- Tier 2 is high-risk or repeatedly expanding work and adds safety review,
  durable evidence, and an independent challenger.

The four profiles are `reader`, `worker`, `reviewer`, and `challenger`.
Acceptance, quality, requirements, safety, and readiness are task charters for
a reviewer, not permanent identities. See `.codex/operating-policy.md` for the
authority and stopping rules.

## Evidence boundary

Track reusable skills, profile declarations, policy, small deterministic
helpers, tests, and `.codex/workflow-control-baseline.json`. Never track private
run records, credentials, local paths, caches, connector payloads, or session
logs.

For a Tier 1 or Tier 2 change, a minimal run record can preserve the target,
checks, reviews, findings, connector state, and delivery ref. Runtime thread and
sandbox fields are operator observations, not cryptographic proof. Review
packets embed the raw Git diff and requirements and are re-derived against live
Git during verification.

## Review and connector stopping rules

Use one fresh review wave, one coherent remediation cohort, and one targeted
re-review per accepted finding family. A remaining blocker stops delivery; an
unrelated improvement is deferred instead of reopening refinement.

Automatic GitHub review is C1. The 👀 reaction on the PR description means it
was picked up, and 👍 there means clean when no conflicting result exists. A
requested C2 uses its exact request comment as the reaction anchor. Pickup is
normally visible within a minute; stop polling after two minutes without it.
After pickup, a large review may take more than ten minutes, so hand off
`IN_PROGRESS` unless the user explicitly asks to keep monitoring. Never send a
duplicate request or infer success from silence.

## Qualification

Run this single bounded gate from the repository root:

```text
python3 .codex/scripts/validate_team.py --self-test
python3 .codex/scripts/validate_team.py --json
python3 -m unittest discover -s .codex/tests -p 'test_*.py'
python3 -m py_compile .codex/scripts/*.py
```

The validator checks generic manifest wiring and complexity budgets; it has no
hard-coded workflow-name matrix. The JSON report emits both current metrics and
the preserved baseline, making later comparisons reproducible.

## Preserved comparison

The superseded implementation is anchored to commit
`d9d4be28116ac1b44ef6da124b93f7d0fba00f39`. These measurements use the exact
definitions stored in `.codex/workflow-control-baseline.json`:

| Measure | Superseded | Simplified |
| --- | ---: | ---: |
| Authority profiles | 19 | 4 |
| Runtime Python lines | 8,383 | 1,306 |
| Test Python lines | 4,179 | 443 |
| Skill entry lines | 1,207 | 280 |
| Largest skill entry | 259 | 56 |
| Maximum mandatory shared reads | 12 | 3 |
| Validator lines | 5,509 | 265 |

The original commit added 17,050 lines across 72 files. Its ignored forward-run
record remained in `REMEDIATION` after 51 artifacts, 27 events, and 18 findings;
it was not qualified. The sanitized counts and qualification basis are retained
in the baseline, while the private runtime artifacts remain ignored.

Repository skills are not tracked work and must not create or require a ticket.
Use the `tracking-exempt` PR path for changes confined to the skills, supporting
`.codex` controls, this guide, or the dedicated validation workflow. Normal
tracking rules still apply when product, release, dependency, security,
architecture, application schema, or application CI behavior also changes.
