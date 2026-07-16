# Exact-Head Pull-Request Merging

## Purpose

`scripts/merge_exact_head.py` is the final fail-closed mutation boundary for an
autonomous slice increment. It checks the live pull request and local evidence,
then asks GitHub to squash-merge only the caller-supplied head commit. It does
not approve reviews, resolve threads, delete branches, change Project fields,
or bypass a repository rule.

The controller first requires server-enforced branch protection with strict
up-to-date checks, approving reviews that dismiss on new commits, resolved
conversations, administrative enforcement, empty review-bypass allowances, and
disabled force pushes and deletion. The repository's current private account
plan does not make that enforcement available. Autonomous slice delivery must
therefore return `BLOCKED` before mutation until the repository is public or
upgraded and protection is configured. This enabling focused change retains its
separate, explicit maintainer merge authority; it does not activate autonomous
slice merging by merging itself.

## Local evidence inputs

Keep review and qualification artifacts outside the tracked checkout or in an
ignored local control directory. Do not commit private paths, reviewer output,
credentials, or private qualification details. Every manifest, envelope, run
record, receipt, and cache record is individually limited to 1 MiB.

The controller reads and hashes the actual bytes. It invokes the caller-named
trusted review-packet validator against the shared COMMIT evidence and every
required envelope, then invokes the run-record validator in qualified mode with
the exact receipt/cache pairs. The run record must be schema 2, use the
`deliver-functional-slice` workflow, bind the qualified base and head, and name
the exact pull-request URL. Syntactically plausible digest strings or summaries
are not evidence.

The caller does not choose which reviews are mandatory. After the trusted
run-record validator passes, the controller derives the exact task set from the
validated workflow and tier: `slice_acceptance` and `code_quality` for every
functional-slice run, plus `safety` and `publication_challenge` for Tier 2.
It requires exactly one trusted-validator-verified envelope whose internal task
matches each derived name, and rejects missing, duplicate, extra, or relabelled
envelopes.

`--review-repo` names the trusted review checkout whose exact files are bound by
the evidence manifest. It may be separate from the candidate checkout. Only the
review-packet validator receives that root; candidate cleanliness, candidate
HEAD, the qualified run record, receipts, and caches are still validated from
the checkout in which the controller runs.

## Dry run and merge

Run from the clean checkout at the expected head. Name every required GitHub
check explicitly. For an ordinary tracked increment, `public-gate` and
`linked-issue` are required:

```text
python3 scripts/merge_exact_head.py \
  --repository OWNER/REPOSITORY \
  --pull-request 123 \
  --base-ref main \
  --base-sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --head-sha bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --required-check public-gate \
  --required-check linked-issue \
  --post-merge-check public-gate \
  --review-repo /TRUSTED/REVIEW/CHECKOUT \
  --review-validator .codex/scripts/review_packet.py \
  --review-evidence /IGNORED/LOCAL/review-evidence.json \
  --review-envelope slice_acceptance=/IGNORED/LOCAL/acceptance-envelope.json \
  --review-envelope code_quality=/IGNORED/LOCAL/quality-envelope.json \
  --run-record-validator .codex/scripts/run_record.py \
  --run-record /IGNORED/LOCAL/run-record.json \
  --qualification-evidence private-cohort=/IGNORED/LOCAL/receipt.json=/IGNORED/LOCAL/cache.json \
  --dry-run
```

Remove only `--dry-run` to authorize the exact merge after inspecting the
machine-readable pass result. The controller:

1. semantically validates and hashes the bounded run-record, review, and
   qualification artifacts, derives mandatory reviews from the validated
   workflow and tier, then requires a clean checkout at the exact head;
2. requires the server-enforcement policy above;
3. checks that the pull request is open, non-draft, unmerged, based on the exact
   named base ref and commit, and reported mergeable;
4. requires each named pull-request check to be an unambiguous pass and the
   review decision to be exactly `APPROVED`;
5. rejects unresolved threads and incomplete thread pagination;
6. rechecks server enforcement, the full pull-request object, every required
   check, and reviews/threads immediately before mutation;
7. sends GitHub the expected head through its merge `sha` guard without an
   admin, bypass, or force option; and
8. fetches `main`, proves the returned squash commit has exactly the qualified
   base as its sole parent and is contained by remote `main`, then boundedly
   waits for each named post-merge check on that merge commit.

Any failure before step 7 performs no mutation. A failure after GitHub confirms
the merge returns JSON with `mutation_occurred:true`, the merge SHA, and a
recovery phase. Do not roll it back automatically: finish verification and
tracking reconciliation, and keep the slice `In progress` until its transaction
reaches `MERGED` or a genuine `BLOCKED` state. Main-push CI is never cancelled
by a later push, so every merge commit receives its own public qualification.
If the merge request times out or returns invalid or malformed confirmation,
the controller queries the live pull request once. A confirmed merged state and
valid merge SHA resume normal post-merge verification. Otherwise it returns
`mutation_occurred:"unknown"` at `merge_response` without retrying the merge or
performing cleanup; a maintainer must reconcile that bounded state first.

## Branch and tracking cleanup

After verified containment, confirm that intended closing references took
effect, update Project state, and delete only the merged delivery branch. The
activation increment additionally verifies the complete supported
demonstration, closes the slice tracker, and reconciles every remaining ticket.
Start the next increment from newly fetched `main`; never continue an obsolete
stack.
