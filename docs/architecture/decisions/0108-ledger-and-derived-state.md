# ADR-0108: Keep owner state in a ledger, regenerate derived state offline, and keep one record of the owner's answer per field

- Status: proposed. Only the owner's own answer makes this record accepted.
  Until then ADR-0013, ADR-0085 and ADR-0096 stay the accepted record, and
  nothing here is built.
- Date: 2026-11-24
- Owners: project maintainers
- Issues: #2037 (this record); built by #2036 (C0, merged), #2038 (C2),
  #2039 (C3), #2040 (C4'), #2041 (C6)
- Amends: ADR-0013 (superseded for the application database from the
  cutover; every other store, report, configuration and document schema
  keeps it), ADR-0085 (manual holds
  replaced by the record, including Amendments 1 to 6), ADR-0096 (carried
  answers under Book matching), and ADR-0014, ADR-0034, ADR-0039, ADR-0049,
  ADR-0057, ADR-0059, ADR-0071, ADR-0082, ADR-0084, ADR-0087, ADR-0095 and
  ADR-0105 as
  the table
  in [Effect on other decisions](#effect-on-other-decisions) says
- Supersedes: none until the cutover
- Superseded by: none

## Owner answers this record rests on

The owner chose this design, "C-minus", as the 1.0 upgrade decision on
2026-11-15 ("1.0 waits for C-minus") and answered its open questions on
decision cards on 2026-11-24:

| Question | Answer | Time (UTC) |
| --- | --- | --- |
| Should a kept, unwritten answer still be written after a re-read? | Yes, full effect | 09:11:07 |
| Approve one record of your answers replacing holds in C2? | Yes, covers included | 09:11:21 |
| Build ledger migrations before 1.0? | Yes, before 1.0 | 09:11:31 |
| File the stage issues now and start C0? | Yes, file and start C0 | 09:12:02 |
| Keep Activity's automatic records across a regeneration? | No, keep them (superseded by the next answer) | 09:11:49 |
| When should Activity start keeping automatic records across upgrades? | Later, as its own change | 10:41 |

Earlier, on 2026-11-15, the owner answered that a later reading asks a field
again "only if its choices changed", and wrote that automatic decisions should
stay on across released upgrades, because "asking users to [re-enable]
automatic after every upgrade is asking too much from a project that aims to
be highly automatic", and that migrations are wanted "shortly after 1.0 or
before".

## Context

Every persisted-shape change is a clean rebuild (ADR-0013). The owner loses
decisions, consents, holds, cover choices, suspensions and write history, and
in store mode the settings and secrets, and repeats first-run setup
([Upgrades](../../operations/upgrades.md#clean-rebuild)). `CHANGELOG.md`
marks about 100 entries **Clean rebuild required** in the pre-release period.
Most of them changed only state the product recomputes from the library and
its providers: reports, proposals, cases, automatic decisions and their
indexes and facts. Stored provider answers can survive a clean rebuild in
their own store when the owner keeps it (ADR-0101), so the expensive part of
re-reading a library need not be lost; the owner's own state still is. ADR-0013 deferred an
upgrade promise to the first production release, and 1.0 cannot make one
while every change discards the owner's answers.

The owner's answer on a field is kept today by a manual hold on each case of
the work, carried to the next case by joining the case transition history
(ADR-0085). Each new reading that the hold did not anticipate has needed its
own amendment: every answer holds (Amendment 1), pins live on the hold
(Amendment 2), cover Resume (Amendment 3), the hold kept across leaving and
turning on Automatic (Amendment 4), held fields in Preview (Amendment 5) and
a pin taken back (Amendment 6). An unwritten answer still lapses at a new case
or episode. A hold is per case, and cases are derived, so the owner's answer
cannot survive a regeneration in that form.

## Decision

### Two partitions in one file

The application database is divided into a **ledger** and **derived state**.

- **Ledger:** canonical owner authority or its audit; a safety budget; or a
  paid, capped artefact that cannot be rebuilt offline. It is versioned,
  backed up and kept by every regeneration. Ledger history that never
  authorises anything (such as no-change rows) is still ledger.
- **Derived:** everything the product recomputes from the library, its
  providers and the ledger. `regenerate-derived` drops it and recreates it
  empty, and preparation rebuilds it. A paid artefact keyed to a derived
  identity, such as a judge verdict on one cover comparison, is derived: it
  cannot outlive the comparison it judges, and the cap that bounds its cost
  is ledger. This qualifier is added by this record, beyond the approved
  design's criterion, for the cover taste tables that landed after it.

Both partitions stay in the one file, so every transaction that writes both
today (recording a decision, admitting a case, persisting a preview outcome,
activating a policy, the attention triggers) stays one transaction. Two files
joined by `ATTACH` would need the attention triggers rewritten as application
code, foreign keys across files and a wider binding and crash recovery, for
no gain over a drop and recreate in one transaction.

Rules for every ledger row:

- **No reference into derived state.** A ledger row never has a foreign key to
  a derived row. It keeps the derived ids it was made against (case,
  proposal, report, fingerprint, consideration) as plain provenance, and a
  reader that finds no matching derived row reads "not current", never an
  error. Derived rows may reference ledger rows. A schema test enforces both
  directions.
- **Item key.** A ledger row about a work carries
  `(connection_name, library_name, item_id)`, and `destination` when it is
  about one field. A rename of a configured library or connection detaches
  owner state exactly as it does today.
- **Copy what is read.** A ledger row stores every value its readers need when
  no derived row matches: the decided value, the display title and previous
  value at decision time, the whole chosen description candidate, the chosen
  cover's digests, target and observed identity, and for write plans and
  no-change rows the display title and connection label. The owner's rows
  also keep the work's authors, for Activity's author label; Automatic's
  keep none, though its no-change rows keep the value they kept.
- **Validation by partition.** Open validates terminal ledger history by its
  shape, its own copies and its ledger references; whether its derived
  provenance is current is a read-time question. Live rows (non-terminal
  plans, current authorities) keep every check they have today, because a
  regeneration revokes them all first. A copy that cannot verify itself
  (a display title, a previous value) is an accepted gap recorded in
  [Schema admission](../security.md#schema-admission) when C2 builds it.

**Classification at c68a3d9.** Ledger:

- owner settings and credentials: `owner_settings`, `owner_setting_secrets`,
  `reviewer_access`, `reviewer_access_secrets`;
- policy: `metadata_policy_drafts`, `metadata_policy_resolutions`,
  `metadata_policy_state`, and `metadata_policy_transitions` with two new
  settled counts (`completed`, `failed`) so a settled transition reads without
  its derived items;
- controls and safety: `automatic_safety_state`, `field_effect_control`,
  `field_effect_suspensions`, `reviewer_task_requests` (the daily paid-request
  cap), and the owner part of `review_preparation_control` (`paused`,
  `first_boot_acknowledged`, `work_identity_level`), split from its
  scheduling part;
- decisions and writes: `human_decisions`, `human_decision_events`,
  `field_effects`, `field_effect_events`, `field_effect_actions`,
  `field_effect_cleanups`, `field_effect_authorities`,
  `field_effect_consents`, `field_effect_no_changes`;
- owner choices split from automatic ones: owner cover selections and the
  owner cover pointer (split from `cover_selection_authorities` and
  `current_cover_selection_authorities`) and owner authored descriptions
  (split from `description_decisions`, with the chosen candidate document
  copied verbatim);
- owner cover taste: `cover_taste_profile_versions`, `cover_taste_builds`;
- retained originals: `retained_cover_bindings`, `retained_cover_listings`,
  `retained_cover_removals`;
- owner links and imports: `owner_links`, `identifier_imports`,
  `identifier_import_events`;
- new with this design: `ledger_meta`, `derived_regenerations`, the record and
  its events (below) and `carry_outcomes`.

`field_effect_manual_holds` is deleted at the cutover; the record replaces it.
Every other table is derived, including `automatic_decisions` and the
automatic halves of the three splits, cases, transitions and episodes,
reports, proposal sets, Book matching level rows, cover considerations and
their events, `cover_automatic_held`, `cover_taste_judgements`,
`cover_taste_verdicts`, description advice attempts and candidates,
preparation state, browse and attention facts, `metadata_report_value_flags`,
`library_visit` (its positions are row ids of derived tables) and
`browse_fact_identity`. Of the two views, `review_attention_refresh` is
derived and `current_owner_links`, over `owner_links` alone, is ledger. Any
trigger whose body writes a derived object, including the
attention triggers on ledger tables. A later table is classified by the same
criterion in the change that adds it.

Automatic decisions are derived because a rotated qualification digest no
longer decodes (ADR-0087): kept in the ledger, every qualification or policy
change would need a migration. What Automatic wrote stays audited by the
self-contained write plans and their events.

The other stores are unchanged: the session database; the provider-recovery
database, whose jobs name a package that no longer exists after a
regeneration and so fail closed; the provider-response store (ADR-0101),
which a regeneration never touches; and the catalogue. The artifact store is
referenced by digest from both partitions. Its sweep also keeps the bytes of
every owner cover pointer's selection, of each work's latest written owner
selection, of every cover answer the record holds, and of every non-terminal
cover plan's desired value, and stops keeping other unwritten owner
selections of earlier epochs and automatic history.

### Identity across a regeneration

`ProposalSet` gains a `derived_epoch` field carried in the proposal document.
Every site that evaluates or re-verifies a proposal set reads
`ledger_meta.derived_epoch` in its own transaction and passes it, so after a
regeneration every proposal set id, review proposal id and case id differs
from every id before it in the database's own history. At epoch 0 the key is
omitted and the decoder refuses an explicit 0, so the ids of a fresh file and
of every test fixture do not change. Within one epoch nothing changes: the
same content recomputes the same ids and episode floors apply as today.

Because every reader that decides whether a decision, authority or plan is
live starts from a lineage-current case id, a decision of an earlier epoch is
history to every reader without a per-row epoch check. The command still
revokes every current authority through the existing revocation, writing the
column and the canonical document together, so ledger-only readers and the
next open read the truth.

### One record of the owner's answer

**The owner's latest answer on a field of a book is kept in one ledger record,
and every automatic path reads it.** This replaces manual holds. While the
record holds an answer, Automatic may still record its own decision for the
field, so Resume can act, but plans no write from it. The record is replaced
by the owner's next answer and cleared only when Resume automatic handling
completes, or when a withdrawal leaves no answer from an earlier case to fall
back to. A re-read, a new case, a new episode, the book leaving and returning,
a policy change, a regeneration and `--retire-pending` never clear it.

`owner_field_answers` holds one row per
`(connection_name, library_name, item_id, destination)`:

- the answer: `source` (`human`, a `human_decisions` row, carried rows and
  cover answers included; or `authored_description`, an owner authored
  description) and its id;
- `ordinal`, from one ledger counter taken in the answer's transaction. Every
  `human_decisions` insert, withdrawals, `matching_changed` rows and carried
  rows included, takes its ordinal from this counter, and an owner authored
  description stores its ordinal in its own row;
- copies so no reader needs a derived row: the outcome and value document (for
  a cover, the chosen candidate's transport and preview digests), the display
  title and when it was recorded;
- the question halves the answer was given against: `offer_digest` and
  `seen_target_digest` (see [The question check](#the-question-check));
- `review_case_id` and `episode` where it was given, used for history, the
  withdrawal rule and the question check's trigger, never as a lapse test. The
  episode joins the human decision document (its next version), so it is a
  ledger fact;
- `previous_answer_id`, the answer a withdrawal restores;
- `carry_eligible`, false once a withdrawal restored this row, a
  `matching_changed` row superseded it, `--retire-pending` retired its plan,
  or the question check closed a failed Resume over it;
- `open_resume_id`, the open Resume request or null;
- `pin_fingerprint`, set by `pin` and cleared by `unpin`.

It is written in the answer's own transaction at every owner-answer site:
recording a human decision (a cover answer is the same write, with the owner
selection id copied), the owner's choice of advised description text, and a
carried answer. Book matching's `matching_changed` row is not an owner answer:
it leaves the record in place and clears `carry_eligible` when it supersedes
the record's answer.

`owner_field_events` is the append-only history beside it, with kinds
`resume_requested`, `resume_completed`, `resume_failed`, `resume_superseded`
and `retired`. Each event names the item key, destination, the scan and the
`over_ordinal` (the counter's value when Resume was asked). It replaces the
hold document's pending return and release token, and feeds Resume's states
on the case page and Activity's history of Resume.

**The record is a pure function** of owner answers in ordinal order (carried
rows included), withdrawals, `matching_changed` rows and the events. A test
asserts that the stored record equals the recomputed one after every proof
case. No seed is needed: C2 and C4' land together behind one clean rebuild, so
the record starts empty and no answer predates it.

**Pins** keep their behaviour (ADR-0085 Amendment 2), on the record instead of
the hold: `pin` sets `pin_fingerprint` on the standing answer's row, or makes
the pin row the record's answer where none stands, and `unpin` clears it,
leaving an ordinary answer. A pinned field is asked again only when its value
differs from the fingerprint, with the pin's own reason, and a case is kept
open while a pin reads changed (ADR-0105 Amendment 1).

**Covers** are on the record too ("covers included"). A cover answer writes
the record. The automatic cover mint returns when the record holds a cover
answer for the work, beside today's owner-pointer test, and the cover arm of
the planning query and the apply-time check read the record as well, so
Automatic never replaces an owner's cover while the record holds it. A cover
row also keeps whether its answer took the cover over from Automatic (an
automatic cover decision on its case, or a record answer that had already
done so), which is where a cover hold is set today. Cover Resume (ADR-0085
Amendment 3) is restated over the record: it is offered only on such a row,
where the derived `cover_automatic_held` mark shows the measured stage would
switch but for the owner; its request and completion are `owner_field_events`; and
completion clears the record row and releases the work's owner pointer,
whether it names the record's selection or one a regeneration repointed it
to, before admission of the current comparison runs again. Who is offered
cover Resume is therefore unchanged: a cover chosen in To decide still never
shows it and is never switched. Covers are not carried by the question check.

**Ported rules.** The hold rules that landed before the cutover become rules
over the record in C2, with no behaviour the owner sees dropped: every answer
keeps its field (Amendment 1); a written value held as the owner's answer; a
withdrawal after leaving Automatic; the answer and Resume kept across leaving
and turning on Automatic (Amendment 4); the held line in Preview
(Amendment 5); a pin taken back (Amendment 6); and going back after a write
(ADR-0105). The automatic mint keeps the exemptions Amendments 4 to 6 added,
read over the record instead of a hold on the case: where the record holds the
field's answer, the case's latest answer for the field is not a withdrawal,
and either Automatic decided the field on the case in an earlier generation
or the owner only pinned it and took the pin back, Automatic still records
its decision, so the case shows the held choice, Activity's "Held by you"
note and Resume.

### Resume automatic handling

- **Offered from the record**, not from the case: on the book's page wherever
  the record holds an answer for a text field or the description, the work
  has a current case, the field is set to Automatic and Automatic's shared
  safety conditions pass for that site (`AUTOMATIC_SITE_CONDITIONS`, C0). A
  field answered in Preview, while paused, under Ask me, or carried while
  Automatic was off can therefore always be handed back. Resume is not offered
  while a pin is in force, as ADR-0085 Amendment 2 shipped (r7.2 N16 had
  Resume unpin first; the shipped rule stands). C2 declares the Resume offer
  as a site in that map.
- **Request.** Writes `resume_requested` with `over_ordinal`, sets
  `open_resume_id`, requeues the projection of the work's current case and
  starts a scan.
- **During and after a Resume** the automatic mint's conflict test and
  automatic description admission ignore owner rows for the destination at or
  below the open or latest completed request's `over_ordinal`, and valueless
  cause rows at any ordinal, so the requeued projection records Automatic's
  decision and a later return to an earlier case is not blocked by an answer
  the owner handed back.
- **Completion**, after the requested scan succeeds: if the record's ordinal is
  at or below `over_ordinal`, one transaction revokes the authority of every
  owner answer the request covers (closing a `planned` or `retryable` plan of
  one as superseded), deletes the record row, re-arms the active generation's
  automatic authority for the field on the current case if there is one, and
  writes `resume_completed`. It waits while a covered plan is `claimed`,
  `applying` or `reconciling`. With no automatic decision to re-arm (the field
  abstains, a bundle partner still has a record, the projection was stale),
  the field is asked in Attention and the next case is decided by Automatic.
- **An answer during a pending Resume** has a higher ordinal, so completion
  leaves it, and the request closes `resume_superseded`. A withdrawal that
  restores an earlier answer keeps the request open; one that empties the
  record closes it `resume_superseded`.
- **Failure.** A requested scan that ends with failures writes
  `resume_failed` and leaves the record and the request; a later successful
  run of the same case still completes it. A request whose latest event is
  `resume_failed` does not count as open for Attention, so the field is asked
  rather than hidden while scans keep failing, and the question check's step 0
  closes it at the next case without carrying.
- **The owner's arm after Resume.** The planning query plans an owner's row,
  human or authored description, only while the record names it. A carried
  row also takes the automatic arm's package join, so it plans only once its
  case is published. After
  completion an unwritten owner answer can no longer plan beside re-armed
  Automatic, and a replaced, restored or withdrawn answer never plans.

### The question check

**When.** At the first projection of a book's case whose
`(review_case_id, episode)` differs from the record answer's (a new case, a
return to an earlier case in a new episode, within a release or after a
regeneration), inside the projection's transaction after its automatic block
and on every path out of it, including Preview and the early returns. The
case is staged there, not yet published, so a carried row is recorded under
the staged-currentness guard the automatic mint uses, in place of the
current-case checks of an owner's request; mint and carry commit together.
A refused or never-run check is retried at startup, dormant start and scan
start, and when a deferring plan reaches a terminal state. Each answer, case
and episode gets one `carry_outcomes` row; a requeued projection of the same
case does not repeat it. `resume_pending` and `deferred` are provisional and
replaced by the retry's outcome; every other outcome is final.

**The question.** `decision_question` (`decision-question-v1`) is the offer
digest's document without proposal ids, split in two halves: the **offer
part**, every offered alternative's value and lineage rule for the whole
title, subtitle and series group; and the **target part**, the
Audiobookshelf state and value of the answered destination only (with the
subtitle where the answer's plan writes it as a companion, or, for an answer
not yet planned, where the planner's bundle rule would write it at the
check). Because the title,
subtitle and series group is one question, a title answer is asked again when
only the series offer changed. A provider-consensus title election that runs
only while the target holds the book's own EPUB title is an expected non-carry
too: after the owner's own write the offer gains the EPUB's decorated title,
so that answer is asked again once, and the answer given then carries.

**Steps**, for each text field and the description with a record row:

0. A Resume is open: no carry; outcome `resume_pending`, run again when the
   request closes without completing. A request whose latest event is
   `resume_failed` is not open: instead, the owner's last instruction for the
   field was to hand it back, so nothing carries and nothing plans. The
   request closes `resume_superseded`, the record loses carry eligibility,
   and the outcome is `asked_again`, so the field is asked in Attention and
   Resume is offered again. This is the design thread's ruling on the record
   (2026-11-24), replacing r7.2's "a carry … in between supersedes it"; the
   owner may overturn it.
1. Not carry-eligible: outcome `asked_again`.
2. The answer's plan is in flight (`planned`, `claimed`, `applying`,
   `reconciling`, `retryable`, or `stale` for its connection, which ADR-0039
   restores when the connection returns): outcome `deferred`, resolved by the
   retry.
3. **Same question?** The new case's offer part must equal `offer_digest`, and
   its target part must equal the expected target: the answer's own value if
   it was written (a verified plan or a no-change row for it or its carry
   chain), otherwise `seen_target_digest`. A plan stopped for the owner
   (`third_value`, `target_changed`, `readback`, `restore_unavailable`) never
   matches.
4. **Same question: carry.** The server records `HumanDecision.carried` through
   the ordinary recording steps. An unwritten `accept` or `owner_entered` is
   carried with its original's application, so it plans under the consent
   current when it plans and every guard. A written or settled answer, and
   every `keep_current` and `reject`, is carried with a new application value,
   `settled`, admitted on both media and only on a carried row: it settles
   Attention and plans nothing. It is not ADR-0071's `recorded_only`, which
   stays F45 history that only an audiobook case may carry, with its own
   historical reason. The carried row replaces the record's answer and
   ordinal, and the record takes the new case's `(review_case_id, episode)`
   and halves, so later projections of that case do not run the check again.
   It names its root original (`carried_from`) as `previous_answer_id`, keeps
   carry eligibility, and an agreeing automatic authority on the new case is
   closed as a replacement closes it today. Outcome `carried`.
5. Otherwise (a different question, a field absent or not actionable, an
   unwritten corrective `keep_current`, an authored description, or a
   constructor refusal): no carry, the record stays, and the outcome is
   `question_changed`, `target_changed`, `asked_again` or `invalid`. The field
   is asked again. An authored description is not carried (the approved
   design keeps its own rule): its text is a candidate that description
   advice produced for a request bound to its case, and `decision_question`
   defines no offer part for advice candidates, so the check cannot tell that
   a later case asks the same question. The record still keeps Automatic from writing the
   description until the owner chooses again or uses Resume.

A pinned field compares the library's value with `pin_fingerprint` instead of
the question, and is not carried. A carried row's idempotency digest is derived from its root
original, the case and the episode, so a work that moves X, Y, X, Y gets one
carried row per visit. A withdrawal or a `matching_changed` row is never
carried, because the record never holds one.

**Authority.** A carried answer is an owner decision formed by the server,
not by an owner request. It needs its own constructor, keeps its original's
`cause`, has its own audit event kind, and grants exactly what its original
granted, on a later case or episode of the same book asking the same
question, within a release as after a regeneration. That is the one exception
to "an owner decision is a request with session, CSRF, Origin, target
fingerprint, case currentness and idempotency key" and to "preparation creates
no owner decision" ([security](../security.md#owner-decisions),
[publication](../security.md#publication)).

### Attention

- An active-generation automatic decision, or a current automatic description
  decision, settles a field only when the record holds no answer for the work
  and destination, or holds one with a Resume open. An active-generation
  automatic decision also does not settle while the case episode's latest
  human row for the field, pins and unpins aside, is the owner's withdrawal.
  Book matching's `matching_changed` row is not one: the answer it asks again
  reads as a pin taken back does, decided only where Automatic decided the
  field, held in Preview, and otherwise as without the answer. That field
  is asked where the case offers a decision on it, so an emptying withdrawal
  never leaves it reading as decided by Automatic; Automatic decides it again
  from the book's next case or episode ([Withdrawal and carry
  eligibility](#withdrawal-and-carry-eligibility)). A Resume over the
  withdrawal that has completed, or is open and has not failed, takes the
  withdrawal back, and Automatic's decision settles the field again. The
  description decision has no such case: an owner's description decision,
  keep current among them, removes the case's current description decision,
  Automatic authors none while such a row counts, and the owner cannot
  withdraw a description.
- Everything else that settles a field today still does: the case episode's
  latest human row that is not a withdrawal (carried rows, `settled` ones
  included), an authored description, and the owner cover pointer. An owner
  row at or below the latest completed Resume's `over_ordinal` no longer
  settles the field, and the case page does not show it as the field's
  decision.
- A field also counts as settled while its latest carry outcome for the case
  and episode is `resume_pending` or `deferred`, and while the record holds a
  `pin_fingerprint` equal to the library's current value for the
  destination, on any case or episode. A pin that reads changed asks with the
  pin's own reason.
- **New reason `asked_again`:** the record holds an answer, the case episode's
  latest human row for the field is absent or a withdrawal, and either the
  latest outcome is `question_changed`, `target_changed`, `asked_again` or
  `invalid`, or the record is not carry-eligible. A pinned field never takes
  this reason; it is asked with the pin's own. The field shows the earlier
  answer and why it is asked: the choices changed, the value in
  Audiobookshelf changed, the owner asked to be asked again, a Resume the
  owner asked for did not finish, or the planned change was retired. This reason is distinct from Activity's existing
  `asked_again` note, which marks an answer that lapsed when a work came back
  to an earlier case in a new episode; under the record that lapse happens
  only when the question check asks again, and C3 words the note to match.

| Situation on the new case | Asked? | Automatic writes? |
| --- | --- | --- |
| Same question, answer unwritten | no (carried) | no; the owner's value writes once under consent |
| Same question, answer written or a refusal | no (carried as `settled`) | no |
| Choices changed, field set to Automatic | yes, `asked_again` | no, until Resume |
| Choices changed, field under Ask me or in Preview | yes | no |
| Value changed in Audiobookshelf | yes | no, until Resume |
| Withdrawn, retired, or Book matching changed it | yes | no, until Resume |
| Resume requested | no | Automatic's decision plans once Resume completes |
| Resume failed, then a new case | yes, `asked_again`; Resume offered again | no; the owner's earlier answer is not written |
| Resume completed, no automatic decision on the case | yes, with the field's own reason | on the next case |

### Withdrawal and carry eligibility

- **Restore from earlier cases only.** When a new answer replaces the record,
  its `previous_answer_id` is the replaced answer if that was given on a
  different case or episode, and otherwise the replaced answer's own
  `previous_answer_id`. A carried row counts as given on its root original's
  case and episode. So A then B on one case, then withdrawing B, leaves no
  answer from that case.
- **Withdrawing the record's answer** restores `previous_answer_id` when no
  completed Resume covers it, with `carry_eligible` false (the owner said
  "ask me again": Automatic stays off, nothing carries or plans), and
  otherwise deletes the row, so Automatic decides the field at the book's next
  reading. Withdrawing a carried row therefore restores its original as not
  carry-eligible. Whether a carried copy of a written answer can be withdrawn
  follows `carried_from` to the original's plans.
- **Withdrawing another answer** leaves the record alone; a withdrawal after a
  completed Resume finds it empty and leaves it empty.
- **A withdrawal is refused beside an automatic decision** on its case only
  where that decision settles the field under [Attention](#attention): when
  the record holds no answer for the destination, or holds one with a Resume
  open. Beside a record answer an automatic decision plans nothing, so it does
  not refuse the withdrawal. This matters on a new case, where the question
  check runs after the automatic block, so a field set to Automatic can hold
  both Automatic's decision and the carried row; the owner can still withdraw
  the carried row before it writes. A withdrawal that deletes the record
  also revokes, in its transaction, the authority of an automatic decision
  already on its case for the destination, so that decision does not start
  to settle or plan; the case's latest row is then the withdrawal, so
  Automatic decides the field from the book's next case or episode. No other
  case-scoped withdrawal rule remains.

### Readers

Every automatic path reads the record by item key and destination instead of
a hold on the case: both copies of the planning query (the automatic arm, the
description arm and the cover arm), the title and subtitle bundle, the
apply-time refusal of an automatic plan, the automatic field, cover and
description mints, automatic description advice's authority, the Attention
clear test and reasons, the case page's held state and the pending Resume
work list. Title recovery keeps today's case-scoped
clause: it does not requeue a case holding an owner decision on its title.
The hold table, its
document and validator, its writers and readers, the hold carry at admission,
and the released-row tests are deleted.

### Startup

Startup stays read-only before any mutation and reads `ledger_meta` first:

| File | Startup |
| --- | --- |
| absent | created fresh |
| ledger exact, derived exact | opens |
| ledger exact, derived from another build | refuses before any write and names `regenerate-derived` |
| ledger from another build, or no `ledger_meta` | refuses: clean rebuild required until C6; after C6, names `regenerate-derived`, which migrates the ledger |
| an extra or altered object in either partition | refuses, as today |

Startup never drops, migrates or regenerates. Each refusal raises one
exception family, `PersistedStateRefusal`, which first-boot detection passes
through unchanged, so the owner sees the message in file and store mode;
every other error keeps failing closed as it does today.

### The offline command `regenerate-derived`

The only operation that changes derived state outside ordinary work. The
owner runs it on the stopped volume after their own backup:
`docker compose run --rm mechanical-librarian regenerate-derived`. The
container entrypoint accepts exactly three argument vectors: none (the
server, unchanged), `regenerate-derived`, and
`regenerate-derived --retire-pending`; anything else exits 2 with a one-line
usage before anything is opened. The command never contacts Audiobookshelf or
a provider, and is composed with no network client.

1. **Admission.** It opens the file through the same binding as the server, so
   it refuses while a server holds the volume. A ledger-only validator is
   passed to all three validation points of the open, so a killed old server's
   journal is recovered against the ledger alone, and derived admission is
   never lent to a dependent. The ledger must be exact (format version, every
   object, canonical documents). Before C6 a ledger mismatch refuses as clean
   rebuild required. After C6 admission also accepts a ledger at an earlier
   version that this build's chain reaches, validated at that version without
   changing it; the migration itself runs only in step 6, after the retained
   backup. `integrity_check` and a foreign-key check of every ledger table
   must pass. It refuses with
   nothing to do only when no ledger migration is pending and the derived
   partition is already exact; a pending ledger migration with a current
   derived partition still runs. The volume must hold the
   retained backup plus three file sizes (the rollback journal and crash
   recovery's copies of the file and journal).
2. **The gate, from ledger rows only.** It always refuses while a plan is
   `applying` or `reconciling`, or `retryable` after an `applying` event.
   Without `--retire-pending` it also refuses while a plan is `planned`,
   `claimed`, `stale` for its connection, or `retryable` with no `applying`
   event, a Resume request is open, or the active policy generation's
   transition is still `processing`; with the flag, step 4 retires these
   instead. It names, per blocking item,
   the reasons it can compute offline (writes paused, a destination or
   connection suspended, preparation paused, the library no longer
   configured), and otherwise says to start the previous build and read
   Activity.
3. **The report**, per library, naming items only by item key and
   destination, never a title, author, path or value: (1) unwritten answers,
   kept if the same question is asked again and otherwise asked again (with
   `--retire-pending`, which of them are retired: asked again, Automatic stays
   off); (2) written answers, counted; (3) authored descriptions not yet
   written; (4) owner cover choices not written, and what each work's owner
   pointer will name afterwards; (5) plans stopped for the owner, which leave
   Activity's owner-action count. Counts are upper bounds. It also says when
   an earlier retained copy will be replaced.
4. **Decision rule.** With no flag it proceeds only when nothing blocks and
   lists 3, 4 and 5 are empty; otherwise it prints the report, writes nothing
   and exits non-zero. `--retire-pending` is the owner's confirmation after
   reading the report: it moves `claimed` plans to `retryable` (`not_sent`),
   closes `planned`, `retryable` with no `applying` event and connection-stale
   plans as `superseded` (`authority_superseded`) through the existing
   revocation, closes open Resume requests `resume_superseded`, and settles a
   `processing` transition as `completed_with_failures`. `applying`,
   `reconciling` and `retryable` after an `applying` event refuse even with
   the flag.
5. **Retained backup.** Before the transaction, under the held lock, it fills a
   private scratch copy with `VACUUM INTO`, syncs it, verifies it with
   `integrity_check` and a digest of every table against the source, and renames
   it to `/data/regeneration-backup/epoch-<n>.sqlite3` (directory `0700`, file
   `0600`, an unsafe existing entry refused), replacing any earlier copy. When
   the volume is short only because it holds the copy of this same epoch (a
   killed or failed earlier run), and that copy's ledger digest equals the
   database's, its size counts as free and it is removed immediately before
   the new copy is taken; otherwise the free-space refusal stands. Crash
   recovery ignores that directory, and first-boot detection ignores it when it
   is a private (`0700`) directory of the runtime user, failing closed on any
   other entry under that name. The artifact sweep
   does nothing, and retained-cover removals wait, while it holds a copy, for
   every caller, so the copy and the artifacts stay a pair. The owner restores it with the previous release,
   setting the provider-recovery store aside (the provider-response store
   stays), or deletes the directory once satisfied.
6. **One transaction**, `BEGIN EXCLUSIVE`, foreign keys on,
   `secure_delete=FAST`:
   1. drop every object that is not in the build's ledger list, triggers and
      views first, then children before parents;
   2. ledger writes, from ledger rows only: first any pending ledger
      migration (see [Ledger format and migrations](#ledger-format-and-migrations)); then, with the flag, the retirements of
      step 4, and every record whose answer's plan the run retires loses carry
      eligibility and gets a `retired` event; every current authority is
      revoked through the existing revocation; each owner cover pointer that
      names a written, kept-current or rejected selection is kept, and any
      other is repointed to the work's latest written owner selection or, with
      none, deleted (the record still keeps Automatic from switching that
      cover); `ledger_meta` gets the next epoch and the build's derived version
      and schema digest;
   3. recreate every derived object from the build's DDL and seed the derived
      singletons; the scan table is left empty, so the server's next start
      asks every configured library to scan (a kept preparation pause still
      holds);
   4. `foreign_key_check` empty and `integrity_check` passing (it also checks
      index contents, which `quick_check` does not), or roll back;
   5. one append-only `derived_regenerations` row: epochs and versions before
      and after, build, times, the backup's digest, `decision_ordinal_hwm`
      (the owner-answer counter's value, so a decision at or below it is of
      an earlier epoch) and the counts of every list and every step;
   6. commit.

   A crash anywhere rolls the transaction back and the next run starts again.
   Every row the steps do not name is untouched.

After a regeneration the first-boot handover is not shown again, a kept
preparation pause stays, preparation runs from the start (reusing stored
provider answers under ADR-0101's rules), settled transitions show their
settled counts and Retry refuses for a generation with no item rows, review
history shows earlier decisions from ledger copies, and provider-recovery jobs
from before fail closed. Activity and Attention show one notice per
regeneration, until dismissed, that counts the answers kept and the answers
asked again from the regeneration's first new cases; its words go to the owner
before C3 is pushed.

**Activity's automatic rows of earlier cases are lost at a regeneration** until
a later change keeps them (owner, 2026-11-24, "Later, as its own change"), as
a clean rebuild loses them today. That covers Automatic's decision rows, the
policy recorded and unchanged rows, ISBN refusals, cover safeguards,
description advice attempts, "automatic pipeline unavailable", safety
superseded, and policy transition item detail. Every write plan, its events
and owner rows survive, with the display copies above, and so do all
no-change rows, Automatic's (`automatic_policy`) included: they are ledger
audit under the criterion above, so Activity keeps its "no change required"
rows. The owner's deferral covers only the derived rows listed here. The
cover taste judge runs again, within the ledger's daily cap, for the current
comparisons, so until it has, Automatic switches no cover by taste.

### Ledger format and migrations

- `ledger_format_version` is 1 at the cutover; startup accepts only the exact
  version.
- A **ledger migration** is a forward-only, code-defined function from exactly
  version `n` to `n+1`. It runs only inside `regenerate-derived`, after
  admission and the retained backup, in the same transaction as the derived
  drop, so a migrated ledger never sits beside a derived partition it was not
  built for. It preserves `human_decisions` ordinals and row order, the record
  and its question halves.
- **A ledger change** is any change to a ledger table or document, or to a
  closed vocabulary a ledger document may hold (the policy option catalogue
  and destinations, action vocabularies, setting enumerations, decision
  causes, Book matching levels, the taste profile schema, the retained-cover
  reasons, `decision-question-v1`).
- Authority a migration makes invalid is kept as history with its authority
  row set to not current; a migration never mints authority. A resolution a
  migration rewrites lapses that library's consents, as an activation does.
- A change whose meaning cannot be preserved is not migrated: it declares
  **Clean rebuild required** with its own decision record. There is no
  downgrade, generic SQL, configuration-supplied operation or import.
- **Guards from C4':** a ledger pin (a digest of the ledger DDL, every ledger
  document's schema version and the closed vocabularies, derived from the
  ledger decoders) that fails when it moves without a new
  `ledger_format_version`; a re-encode test that opens the frozen synthetic
  `tests/fixtures/ledger/v<n>.sqlite3`, validates it through the real decoders
  and re-encodes every document byte for byte; and a `derived_version`
  constant pinned to the derived DDL, the evaluation constants and a manual
  semantics revision, which fails when the derived digest moves without a new
  version and a **Derived regeneration required** entry. A semantic-only
  derived change that forgets the revision is caught by review, not by a test.
- **Markers.** Until the cutover lands every persisted-shape change declares
  **Clean rebuild required**, as today. From the cutover a derived-only change
  declares **Derived regeneration required**, and a ledger change declares
  **Clean rebuild required** until C6, then **Ledger migration**.
- **C6 is built before 1.0**, with the first ledger change after the cutover
  as its first consumer. Every ledger migration runs inside a regeneration,
  so the owner's experience of one is the same as a derived upgrade.

\#57's gates hold for every run: integrity before and inside the transaction,
the free-space reserve, exclusivity through the binding's lock and
`BEGIN EXCLUSIVE`, the verified retained backup, restart by rollback,
rollback to the previous release by restoring the retained copy, and audit in
`derived_regenerations`.

### Stages

| Stage | Issue | What it delivers | Persisted format |
| --- | --- | --- | --- |
| C0 | #2036 (merged, c68a3d9) | Automatic's safety conditions defined once, each site checked against its declared set | none |
| C1 | #2037 | this record and the documents | none |
| C2 | #2038 | the partitions, keys and copies, the split tables, the record and its events, per-partition validation, the ported hold, pin, cover Resume, go-back and kept-case rules | **Clean rebuild required** |
| C3 | #2039 | readers and the write lane without derived rows, Activity and review history from copies, the `asked_again` reason and the upgrade notice | none |
| C4' | #2040 | `regenerate-derived`, the epoch, startup refusal, the question check and carry, `ledger_meta` (the cutover) | **Clean rebuild required** |
| C5 | optional | shadow names retired from derived objects | the first **Derived regeneration required** |
| C6 | #2041 | ledger migrations, before 1.0 | **Ledger migration** |

C2 and C4' land together with no release between them, so an owner rebuilds
once. C4' is the measuring stage: on a scratch copy of a retained 250-item
development library, inside the container's memory and `/tmp` limits, the
command must finish in under five minutes, its peak free-space use must stay
inside the gate's reserve and a kill at that peak must recover, and the owner
state proof must fail its negative controls. If the bar is missed, C2 to C4'
do not land, every upgrade stays a clean rebuild and this record is marked
rejected.

### Effect on other decisions

| Decision | Effect |
| --- | --- |
| ADR-0013 | Superseded for the application database from the C4' cutover, which changes no rule before it; every other store, and every report, configuration and internal document schema, keeps ADR-0013, its qualification bridge and its provider-evidence rules. Its exact-current rule becomes this record's startup table, its derived-only adoption route is retired by `regenerate-derived`, its exclusion of "canonical row conversion, general migration machinery" is lifted for the ledger migrations above, and its deferral of post-release migration to #57 is answered by C6. |
| ADR-0085 | From C2 the record replaces manual holds: "Holds follow the work" and the hold carry at admission, the release token, the released-row rules of "Neutral or deferred", and Amendments 1 to 6 are restated over the record as described above. Withdrawal restores only an answer from an earlier case or episode. Owner-entered values, withdrawal, reselection and their refusals are unchanged, except that an automatic decision refuses a withdrawal only where it settles the field (beside no record answer, or one with a Resume open). |
| ADR-0096 | A carried answer keeps its original's `cause`, so a later Book matching change can supersede it with `matching_changed` (which clears its carry eligibility), and a carried `matching_changed` row is never formed because valueless rows are not carried. Retained validation of a terminal decision does not re-check its offer digest against level offers a regeneration dropped; a current case's decisions keep the check. Level storage stays derived. |
| ADR-0014 | Case identity binds the derived epoch through the proposal set. |
| ADR-0034 | The transition header, with its settled counts, is ledger; a regeneration never runs while a transition is `processing`. |
| ADR-0039 | A regeneration revokes every authority through the existing revocation, superseding ready and connection-stale plans with `authority_superseded`. |
| ADR-0049, ADR-0059 | The owner cover selections and pointer are ledger and suppress automatic cover, together with the record; at a regeneration an unwritten owner choice is repointed to the latest written one or cleared. Automatic selections and pointers are derived. |
| ADR-0057 | One file, two partitions; no shared job primitive. |
| ADR-0071 | A carried copy of a written or settled answer takes a new application value, `settled`, admitted on both media and only on a carried row; it plans nothing and reads as the owner's carried answer. F45's `recorded_only` keeps its meaning, its audiobook-only admission and its historical reason. |
| ADR-0082 | Activity reads ledger copies; the rows above are lost at a regeneration. |
| ADR-0084 | Attention triggers on ledger tables are derived objects. |
| ADR-0087 | Automatic decisions are derived; nothing decodes an old qualification digest. |
| ADR-0105 | A clear holds its field through the record, and a case is kept while a pin on the record reads changed (Amendment 1). |
| ADR-0095 | The taste profile and its builds are ledger; profile eligibility reads copies in the owner selection rows, so an owner's earlier picks keep counting after a regeneration. Judgements and verdicts are derived. |

ADR-0077 is not amended: no read admission or currentness-fact rule changes.

### Left to the owner

These change only with the owner's own answer and are not made here:

- `AGENTS.md` "Change requirements" and its ADR-0013 bullet: from the cutover,
  the owner-run offline `regenerate-derived` command is the one exception to
  "never migrates" and to "never a generic migration, import, repair,
  downgrade, or canonical-data conversion", for derived regeneration and, from
  C6, code-defined exact-predecessor ledger migrations; and the
  `derived_version` rule.
- Issue #57: rewritten as the boundary this record sets, closed by C6.
- The status of this record, and milestones.

The stage that makes them true updates `PRODUCT.md`'s persisted-state
sentence and principle 5, `CONTRIBUTING.md` and the pull request template's
markers, [Upgrades](../../operations/upgrades.md),
[compatibility](../../reference/compatibility.md#the-clean-state-rule),
[backups and recovery](../../operations/backups-and-recovery.md), the
[SQLite topology](../sqlite-topology.md) and the
[security model](../security.md).

## Consequences

### Positive

- After the cutover a derived upgrade keeps settings and secrets, consents,
  suspensions and pauses, the owner's answers, cover choices, links and taste,
  and all decision and write history, with no first-run setup.
- An owner's answer survives re-reads and upgrades alike, and is written once
  when it was not yet written, so a report-only owner does not answer again
  what they already answered.
- One record replaces the per-case hold and its carry, deleting the class of
  defect ADR-0085's amendments patched one at a time.
- Automatic keeps running across an upgrade wherever the owner has not
  answered.

### Negative

- Every answered field stays off-limits to Automatic until Resume, everywhere,
  as it has been within a work since Amendment 1.
- A carried answer is an owner decision the server forms. The exception rests
  on the question being a faithful identity: a new build that offers the same
  values with the same lineage rules for different reasons still carries.
- Activity's automatic rows of earlier cases are lost at each regeneration
  until the later change.
- Cover review, description advice and taste judging are paid again for
  current comparisons; undecided fields are asked again.
- The retained backup holds a secret-bearing copy in store mode and stops
  artifact cleanup until the owner deletes it; a restore sets the
  provider-recovery store aside and loses its pending jobs.
- The gate can stay shut while writes are paused, a connection is down or a
  write's outcome is unknown; `applying`, `reconciling` and `retryable` after
  an `applying` event need the previous release to finish them.
- C2 is large (about 15 product files and 36 test files), and C2 and C4' cost
  owners one more clean rebuild.

### Neutral or deferred

- Keeping Activity's automatic rows across a regeneration, for example as an
  append-only archive of their words, is its own later change.
- Binding `--retire-pending` to the report's digest, and a random epoch nonce,
  are possible follow-ups; neither is needed for safety.
- Clean rebuilds still start Activity empty.

## Alternatives considered

### Two stores re-attached by stable keys ("C-full")

Keeping decisions on the same case ids across a regeneration needed an
evaluation tier, case admission registry, ledger episodes and a replay window.
Three review rounds refuted its same-id mechanisms, and it kept decisions on
about one past rebuild in five for roughly twice the code.

### Defer until after 1.0

Every upgrade would stay a clean rebuild, and 1.0 could promise nothing about
upgrades.

### Hold every latest decision at a regeneration

The first form of this design turned every latest owner decision into a hold.
Every decided field would come back held after each upgrade and need Resume
one at a time, the chore the owner rejected.

### Carry only blocking

A carried answer would settle Attention and keep Automatic off but never
plan, so every unwritten value is asked again after each upgrade. It avoids
the authority exception; the owner chose full effect.

### Keep automatic decisions in the ledger

Every qualification rotation would need a migration, or the open would have to
decode old digests, reversing ADR-0087.

### Regenerate silently at startup

It would retire unwritten decisions and in-flight writes without the owner's
choice, and startup would gain migration authority.

## Validation

Documentation checks and the document-versions test prove C1. Each later
stage proves its part against the fake Audiobookshelf target through the real
admission, evaluation, planning query and planner:

- **C0 (merged):** every site checked against its declared condition set over
  the full posture and safety matrix.
- **C2:** no ledger table has a foreign key to a derived table, and every
  ledger document decodes and the file opens with every derived row deleted;
  each relaxed validator refuses a mutated copy, and a live plan's checks are
  not relaxed; the record equals its recomputation after every case below;
  pins, cover Resume, the hold fixes, going back and the kept case behave as
  before on the record.
- **C3:** Activity, review history and the write lane run with every derived
  row of earlier cases deleted, reading copies, and never raise.
- **C4' and C2 together:** the six cases of the automatic decisions audit
  (title corrected after Automatic's read-back, a previous value put back,
  answers given in Preview, while paused and under Ask me) write nothing
  automatically on any later read; genres; an unwritten answer written exactly
  once and Automatic's after Resume; Resume on a field that abstains; Resume
  during a re-read; Resume, then a failed scan, then a same-question re-read
  plans nothing, shows the field as asked and closes the request superseded; withdrawal in each order; `--retire-pending`; a plan in
  flight across a re-read; every row of the Attention table, set to Automatic
  and in Preview; a return to an earlier case; a group target; description
  advice for both owner sources; covers; and the owner state across a
  regeneration on a file whose derived partition comes from a frozen earlier
  release. Negative controls: without the record clause on the automatic arm,
  without the owner-arm record test, without step 0, without the Attention
  clause, with the expected target replaced by the seen target alone, and with
  an epoch source that never increments, the matching cases fail.
- **C4' command:** each blocking state refuses with nothing written; the flag's
  transitions; the backup verified, retained and restorable with the previous
  release; recovery and first boot ignore it and the sweep waits for it; drop
  order, planted objects, append-only audit, value-free output, no network
  client, the three argument vectors, and `PersistedStateRefusal` in both
  modes; and the measuring bar above.
- **C6:** a frozen synthetic v1 ledger migrates to v2 inside the command and
  matches the expected dump, a newer ledger is refused, and a kill during the
  migration rolls back with the retained backup intact.
