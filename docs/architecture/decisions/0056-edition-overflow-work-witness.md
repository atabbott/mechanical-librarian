# ADR-0056: Retain a work-only witness for bounded edition overflow

- Status: accepted
- Date: 2026-08-13

## Decision

When Hardcover returns exactly one row beyond the bounded edition envelope and
all six rows link uniquely to one complete work, retain a closed work-only
witness. It contains the provider work identity, validated title, ordered
author claims, and exact author identity bindings. It is a terminal overflow
state, not a provider candidate, lane candidate, proposal input, or cover
source. Edition identities and fields, raw provider values, and diagnostics are
discarded.

The metadata report advances to v24. Under ADR-0013, v23 data remains rejected
before mutation; there is no migration, import, or conversion.
