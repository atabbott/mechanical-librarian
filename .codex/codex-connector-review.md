# On-demand Codex GitHub review

Codex performs connector round `C1` automatically when a ready pull request
opens. Do not manually trigger C1. The root has standing authority for exactly
one follow-up round, `C2`, when this policy permits it. Never request `C3`,
including after another head, invocation, close/reopen, or replacement comment.

Connector rounds are separate from implementation (`I`) and pull-request
remediation (`PR`) counters.

## Frozen request identity

Record the opening PR head and exact PR `created_at` before interpreting C1.
Record the opening head, requested head, exact trigger-comment database ID, and
exact trigger-comment `created_at` for C2. Never default these values from the
current PR head or the time at which the helper happens to run.

Inspect C1 with:

```text
python3 .codex/scripts/connector_state.py \
  --repo <owner/name> --pr <number> \
  --opening-head <40-character-sha> \
  --requested-at <exact-pr-created-at>
```

Inspect C2 with the same arguments plus:

```text
  --trigger-comment <database-id> \
  --requested-head <40-character-sha>
```

The read-only helper rejects a request timestamp that is not exactly equal to
the relevant target's creation timestamp. It paginates every GitHub collection
read. C1 reactions are bound to the PR description; C2 reactions are bound only
to the exact trigger comment. Reactions placed on a different target never
complete the requested round.

The only accepted normalized C2 trigger bodies are:

```text
@codex review
```

or one single-line narrow focus:

```text
@codex review
Focus: <current requirement or finding family>
```

Whitespace and line endings are normalized before this exact comparison. Do
not accept a mention embedded in prose, an inline suffix, extra paragraphs,
case variations, or another command.

## Observable state machine

The helper reads connector reactions, submitted reviews, issue result comments,
and pull-review comments. It also reads alternate reaction targets for C2 so a
misplaced terminal reaction cannot leave the workflow waiting forever.

- `PENDING_PICKUP`: the round preserves the exact request creation time and its
  first honest helper observation sees no connector pickup signal while less
  than two minutes have elapsed;
- `IN_PROGRESS`: the connector added `eyes` on the exact request target;
- `CLEAN`: the connector added `+1` on the exact target and no `eyes` or finding
  result conflicts with it;
- `FINDINGS_POSTED`: a substantive connector review or result comment is bound to the
  requested commit, or lacks a commit field where GitHub provides none;
- `PICKUP_UNOBSERVED`: no pickup or result was visible after the fixed
  two-minute pickup window;
- `TERMINAL_OFF_TARGET`: only a terminal result bound to another commit or a
  terminal reaction on another target is visible;
- `PROTOCOL_VARIANCE`: connector activity is visible but does not conform to a
  recognized on-target pickup or terminal sequence; and
- `INCONSISTENT`: observations conflict, including simultaneous `eyes` and
  `+1`, or a clean reaction alongside a finding result.

`PICKUP_UNOBSERVED` is a bounded handoff state, not a permanent conclusion.
Re-run the helper during a later maintenance invocation: a late `eyes`, `+1`,
review, or result comment replaces it with the newly observed state. Never
duplicate C1 or repost C2.

The JSON report is an observation event suitable for history rather than a
mutable snapshot claim. Store its exact canonical JSON in the run event and
store its SHA-256 as the event digest; never replace it with a projection built
from asserted event fields. It includes the observation and request timestamps,
opening/requested/current heads, target kind and ID, reaction/review/result
comment IDs, each observable event's actor, commit binding and timestamp, and
pickup, terminal, off-target-terminal, and observation latency. Preserve
successive reports when history matters. The durable record recomputes state,
variances, ID indexes, head comparison, and latencies from the raw report and
cross-checks its repository, PR, round, target, request time, and requested
head. Do not overwrite an earlier
`PICKUP_UNOBSERVED` observation to pretend pickup was originally observed.
The first report is recorded at its real observation time and may therefore
have positive latency; never backdate it to the request creation time.

Formal reviews and pull-review comments must match the requested commit when
GitHub exposes their commit ID. A mismatched commit is off-target. Issue result
comments without a commit field are reported with `UNAVAILABLE` binding rather
than silently asserted to be commit-bound. A late C1 result remains compared
with the recorded opening head even if the current PR head advanced.

Pickup normally occurs in less than one minute. Use non-blocking polling only
within the two-minute pickup window. At two minutes with no `eyes`, record
`PICKUP_UNOBSERVED`, end the current wait, and do not duplicate the request.
Once `eyes` appears, completion can take
more than ten minutes for a large change. Ordinary delivery may hand off
`IN_PROGRESS` as external state. When the user asks to monitor or babysit, use
recurring non-blocking waits and keep the user informed. Never infer a clean
review from elapsed time or silence.

## C1 completion and C2 eligibility

A conforming clean `+1` completes C1 with no findings and makes C2 ineligible.
A connector result completes pickup, but its claims remain advisory until
evaluated under `.codex/engineering-judgment.md`. Accepted findings enter the
root family synthesis in `.codex/finding-family-audit.md`.

A pending, in-progress, or pickup-unobserved C1 blocks only when branch
protection requires connector completion, but it always blocks a claim that C1
completed. Report the PR as a publication candidate with the exact connector
state.

Request C2 only when:

- the PR is ready, the requested head is frozen, and local qualification and
  required independent reviews are complete;
- C1 produced at least one accepted actionable finding;
- all accepted C1 findings and material discoveries from their investigation
  or remediation are closed;
- qualified fixes were pushed after affected local reviews and gates passed;
- no C2 trigger, reaction, review, or trigger receipt already exists; and
- no user instruction prohibits C2.

Do not request C2 when C1 was clean, had no accepted finding, or required no
remediation push. Never request it for a `NOT READY` checkpoint or merely
because a review is stale.

## Terminal trigger and receipt rule

Treat C2 as the final GitHub mutation of a maintenance invocation. Before
posting it, finish all commits, pushes, evidence updates, state refreshes,
replies, thread resolutions, finding dispositions, and authorized issue or
Project bookkeeping for the entire round. If several PRs are eligible, post
their trigger comments consecutively as the final external mutation batch.

Before that batch, create the append-only local receipt destination and freeze
all receipt inputs. Stream each POST response directly into one receipt append;
do not perform a follow-up GitHub read to rediscover its ID or timestamp. The
one permitted post-trigger local mutation is exactly one append per successful
trigger to `connector-trigger-receipts.jsonl` in the current private run
directory. Each JSON line contains only:

```text
event=CONNECTOR_C2_REQUESTED
repo, pr, opening_head, requested_head
trigger_comment_id, requested_at, normalized_trigger
recorded_at
```

`trigger_comment_id` and `requested_at` come directly from the successful POST
response; the heads and normalized trigger come from the pre-frozen request.
The append must not read Git or GitHub state, rewrite an earlier line, validate
the run, update another record, or perform any other mutation. A failed or
ambiguous POST produces no receipt and stops. Fold the receipt into the main
structured record and validate it only in the next invocation.

After the final trigger-response append, perform no Git, GitHub, repository,
polling, verification, or mutation action in that invocation. Observe reactions
and results with the read-only helper in the next invocation. This narrow
append exception resolves the need to retain the returned comment ID and exact
timestamp without weakening the final-mutation boundary.

If C2 pickup is later unobserved, do not repost the trigger: the one manual
request has already been consumed. After C2, close accepted findings through
bounded local remediation, qualify and push normally, and never request another
connector review. Do not post `@codex fix` or another task instruction under
this authority.
