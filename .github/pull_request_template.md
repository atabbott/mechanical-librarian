## Summary

Describe the problem and the resulting behavior.

## Linked work

Closes #

Parent issue or work package:

Governing specification or ADR:

## Change

Describe the implementation at the level a reviewer needs to evaluate it.

## Risk and recovery

Describe security, privacy, compatibility, data, migration, deployment, and rollback impact. Write `None` only after considering each category.

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

- [ ] The change is narrowly scoped and contains no unrelated refactoring.
- [ ] No credentials, private URLs, personal paths, copyrighted media, or production data are included.
- [ ] New failure states are explicit, observable, and recoverable.
- [ ] Persisted or public contracts include migrations and compatibility notes.
- [ ] Durable architectural decisions add or supersede an ADR.
- [ ] User-visible behavior updates the changelog and documentation.
