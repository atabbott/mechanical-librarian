# GitHub connector review protocol

Opening a ready pull request starts automatic round `C1`. Never post a duplicate
manual trigger. One explicit follow-up round, `C2`, is allowed only after
actionable C1 findings are fixed.

## Request identity

Record the request target and head before interpreting reactions:

- C1: opening head, PR number, exact PR `created_at`, and PR description;
- C2: requested head, exact trigger-comment database ID and `created_at`, and
  that request comment.

Inspect state with `.codex/scripts/connector_state.py`. C1 reactions must be on
the PR description. C2 reactions must be on the exact request comment.

## Signals

- `eyes` (👀): the connector picked up the review and is working;
- removal of `eyes`: pickup ended, but completion still needs a result;
- `+1` (👍): clean review when it is on the correct target and no conflicting
  result exists; and
- a substantive review or result comment: findings, bound to the requested
  head when GitHub exposes a commit.

Unexpected, conflicting, off-target, or unbound activity is protocol variance,
not success.

## Waiting

Pickup normally appears in under one minute. Poll non-blockingly for no more
than two minutes. If no `eyes` or result is visible at two minutes, record
`PICKUP_UNOBSERVED`, stop waiting, and never duplicate the request.

After `eyes`, a large review may take more than ten minutes. An ordinary
delivery may hand off `IN_PROGRESS`; only a user request to monitor authorizes
continued recurring waits. Silence never means clean.

The helper reports `PENDING_PICKUP`, `IN_PROGRESS`, `PICKUP_UNOBSERVED`,
`CLEAN`, `FINDINGS_POSTED`, `TERMINAL_OFF_TARGET`, `PROTOCOL_VARIANCE`, or
`INCONSISTENT`. Preserve its canonical JSON when durable evidence is useful.

Do not request C3, merge, approve, or resolve findings merely because the
connector is optional.
