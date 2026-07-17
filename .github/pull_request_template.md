## Summary

Describe the problem and the resulting behavior.

## Linked work

Closes #

## Tracking exemption

<!--
Leave this section blank for normal tracked work. A maintainer may apply the
exact `tracking-exempt` label and replace this comment with a concise rationale
only for eligible trivial cleanup described in the work-management policy.
-->

## Delivery mode

- [ ] New-functionality slice increment
- [ ] Focused ticket: defect, bug, security, documentation, tooling, dependency, maintenance, or standalone work

Slice tracker, when applicable: #

Increment and activation status, when applicable:

Additional constituent tickets closed in full by a slice:

- Closes #

Parent issue or work package:

Governing specification or ADR:

Branch: `slice/`, `fix/`, `feature/`, `docs/`, `chore/`, or another purpose-based name

## Functional outcome

For a slice increment, describe its independently safe vertical step and how it
advances the frozen slice outcome. Identify the activation demonstration when
this increment closes the tracker. For focused ticket work, describe the
corrected or bounded outcome.

Demonstration steps:

```text

```

## Change

Describe the implementation at the level a reviewer needs to evaluate it.

## Risk and recovery

Describe security, privacy, compatibility, data, migration, deployment, and rollback impact. Write `None` only after considering each category.

For a Defect or Bug, include the affected revision, pre-fix reproduction, root
cause, regression evidence, and any workaround or recovery implications.

## Verification

- [ ] Formatting and static checks
- [ ] Unit or property tests
- [ ] Contract or integration tests
- [ ] Failure, retry, and idempotency behavior
- [ ] Security or hostile-input tests where relevant
- [ ] Documentation and generated contracts
- [ ] Container, migration, backup, or recovery checks where relevant

List the exact commands and outcomes:

```text

```

## Review checklist

- [ ] The selected delivery mode is accurate; fixes and standalone work have not been relabelled as slices.
- [ ] If this is a slice increment, it is independently safe and demonstrable; if it closes the tracker, it completes the documented outcome and exit criteria.
- [ ] Every auto-closed ticket is satisfied in full; no partial acceptance is hidden in follow-up work.
- [ ] The relevant demonstration or reproduction works from a clean checkout.
- [ ] The change is narrowly scoped and contains no unrelated refactoring.
- [ ] No credentials, private URLs, personal paths, copyrighted media, or production data are included.
- [ ] New failure states are explicit, observable, and recoverable.
- [ ] Persisted or public contracts include migrations and compatibility notes.
- [ ] Durable architectural decisions add or supersede an ADR.
- [ ] User-visible behavior updates the changelog and documentation.
