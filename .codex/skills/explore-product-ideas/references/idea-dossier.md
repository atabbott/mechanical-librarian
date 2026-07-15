# Idea dossier template

Create `.codex/ideas/INDEX.md` on the first approved dossier. Keep the index as
a compact table with `Idea`, `State`, `Updated`, `Summary`, and `Dossier`.

Use a short purpose-based kebab-case filename such as
`bundled-local-reviewer.md`. Avoid implementation names until the decision
actually depends on them.

```markdown
# <Idea title>

State: <CONTINUE EXPLORING | AWAIT EVIDENCE OR DECISION | PARK FOR LATER | REJECT IN CURRENT FORM | READY TO HAND OFF | HANDED OFF | COVERED BY EXISTING WORK>
Created: YYYY-MM-DD
Updated: YYYY-MM-DD

## Current synthesis

<The shortest accurate statement of the current idea, why it may matter, and
the current direction. Rewrite this as understanding changes.>

## Problem and desired outcome

- Problem:
- Beneficiaries:
- Observable improvement:
- Why now:
- Non-goals:

## Evidence and claims

### Verified facts

- <Claim and repository location or dated source reference>

### Inferences

- <Inference and evidence it rests on>

### Assumptions

- <Assumption and how to test it>

### Preferences and decisions

- YYYY-MM-DD — <Owner preference or confirmed decision and rationale>

### Open questions

- <Question, why it matters, and next evidence or decision needed>

## Options and trade-offs

### <Option>

- Value:
- Product and architecture fit:
- Risks and costs:
- Reversibility:
- Evidence gaps:

### Do nothing or defer

- Consequence:
- Revisit condition:

## Current work-plan fit

- Proposed disposition:
- Related milestones, slices, issues, or deferred work:
- Prerequisites and dependencies:
- Work displaced or delayed:
- Product-contract or governance implications:
- Why this fit remains provisional:

## Risks and constraints

- Security and privacy:
- Deployment and operations:
- Compatibility and migration:
- Qualification and maintenance:

## Decisions and discarded paths

- YYYY-MM-DD — <Decision or discarded path, rationale, and reversal condition>

## Recommended next step

- Proposed state:
- Next action:
- Owner confirmation:
- Reopen or reversal condition:

## Handoff outcome

- Handoff date:
- Readiness disposition:
- Resulting trackers or issues:
- Approved owner of next work:
- Remaining follow-up:

## Sources

- Accessed YYYY-MM-DD — [<Primary source title>](<URL>): <Claim supported>
- Repository — `<path or symbol>` at <commit or current working state>: <Claim supported>

## Session log

### YYYY-MM-DD — <Short session label>

- Questions explored:
- Understanding gained:
- Material changes:
- Remaining tension:
```

Keep citations adjacent to research claims where practical as well as in
`Sources`. Do not paste long source text. Preserve the session log as history,
but keep detailed conversational transcripts out of the dossier unless a
specific quotation is necessary to retain a decision accurately.
