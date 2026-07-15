# Model selection and root escalation

Use capability proportionately. Terra handles routine exploration and
implementation. Sol performs every independent review: medium for bounded
evidence evaluation and high for whole-diff quality, safety, publication
challenge, root reconciliation, and final qualification.

| Work | Roles | Required model / effort |
| --- | --- | --- |
| Root orchestration, reconciliation, qualification | root | `gpt-5.6-sol` / high |
| Exploration, research, readiness analysis, triage, reproduction | explorer, researcher, analyst, triager, reproducer roles | `gpt-5.6-terra` / medium |
| Normal implementation/remediation | worker roles | `gpt-5.6-terra` / medium |
| Feedback, readiness, acceptance, requirements, defect/focused verification | bounded reviewer roles | `gpt-5.6-sol` / medium |
| Whole-diff quality, safety, publication challenge | quality/safety/challenger roles | `gpt-5.6-sol` / high |

Binding evidence follows `.codex/binding-preflight.md`. A null, wrong, or
unverified reviewer model/effort does not silently satisfy the gate; record a
qualified fallback or stop.

## Implementation escalation

Do not escalate because work is merely large or slow. Escalate to the Sol root
when the bounded implementation requires difficult architectural judgment or
directly changes concurrency, persisted schemas/migrations, authorization,
hostile parsing, recovery/rollback, trust boundaries, or production write
authority.

Classify this before the first edit whenever the high-risk boundary is already
known. In that case, do not start a dummy worker. Record a target/ref/packet-
digest-bound `ROOT_ESCALATION` event, permitted paths/behavior, prohibited
scope, triggering invariant, completed evidence, and expected verification;
then start root's sole-writer interval immediately before its first edit.

If a worker discovers the trigger after editing began, stop its writer interval,
preserve state, record the same bounded handoff and `ROOT_ESCALATION`, then
start root. The worker may resume separable low-risk work only after root stops
and a new explicit handoff grants it. Root and worker never edit concurrently.

Root owns only the escalated boundary; routine implementation remains with the
worker when demonstrably separable. Root implementation never waives the same
independent Sol reviews, evidence-before-review gate, or qualification.
