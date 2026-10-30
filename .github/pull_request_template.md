## Summary

What problem this solves and how the behaviour changes.

Closes #

## Tracking exemption

<!--
Leave blank for normal tracked work. A maintainer may apply the exact
`tracking-exempt` label and replace this comment with a short rationale, only
for spelling, formatting, broken-link or comment-only cleanup.
-->

## Delivery

- [ ] Functional slice increment (slice tracker: #)
- [ ] Focused change: fix, security, documentation, tooling, dependency or maintenance

For a slice increment, say which step of the slice outcome this delivers and
how to demonstrate it. For a defect or bug, give the affected revision, a
reproduction, the root cause and the regression evidence.

## Risk and recovery

Security, privacy, compatibility, data and rollback impact. Write `None` only
after considering each.

## Verification

The exact commands run and their outcomes:

```text

```

## Checklist

- [ ] Every closed issue is satisfied in full.
- [ ] Documentation this change made stale is updated in place, and the
      changelog has an entry for any user-visible behaviour.
- [ ] Persisted-state changes declare a clean rebuild or ship an adoption
      (ADR-0013); durable decisions add or supersede an ADR.
- [ ] No credentials, private URLs, personal paths, copyrighted media or
      production data.
