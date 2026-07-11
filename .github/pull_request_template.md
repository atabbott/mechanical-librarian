## Summary

Describe the problem and the resulting behavior.

## Linked work

Closes #

## Delivery mode

- [ ] New-functionality slice
- [ ] Focused ticket: defect, bug, security, documentation, tooling, dependency, maintenance, or standalone work

Slice tracker, when applicable: #

Additional constituent tickets closed in full by a slice:

- Closes #

Parent issue or work package:

Governing specification or ADR:

Branch: `slice/`, `fix/`, `feature/`, `docs/`, `chore/`, or another purpose-based name

## Functional outcome

For a slice, describe what a user or operator can now do through a supported
CLI, API, browser, or container surface. For focused ticket work, describe the
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
- [ ] If this is a slice, it completes the documented functionality and exit criteria through a supported demonstration.
- [ ] Every auto-closed ticket is satisfied in full; no partial acceptance is hidden in follow-up work.
- [ ] The relevant demonstration or reproduction works from a clean checkout.
- [ ] The change is narrowly scoped and contains no unrelated refactoring.
- [ ] No credentials, private URLs, personal paths, copyrighted media, or production data are included.
- [ ] New failure states are explicit, observable, and recoverable.
- [ ] Persisted or public contracts include migrations and compatibility notes.
- [ ] Durable architectural decisions add or supersede an ADR.
- [ ] User-visible behavior updates the changelog and documentation.
