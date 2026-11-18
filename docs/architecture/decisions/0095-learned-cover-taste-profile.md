# ADR-0095: Learn the owner's cover taste as a readable profile and let a closed judge verdict choose covers

- Status: proposed
- Date: 2026-11-07
- Owners: project maintainers
- Issues: #1372, with the cover review section #1633
- Revises: ADR-0059 ("reviewer observations ... cannot admit, exclude, order,
  delay, or decide"), for this one closed verdict only
- Amends: ADR-0062 (two new reviewer tasks)
- Depends on: ADR-0093 (eligible set, current-cover protection, retained-cover
  revert) and ADR-0094 (unconfirmed current covers)
- Supersedes: none
- Superseded by: none

## Context

ADR-0093's deterministic stage keeps a fault-free current cover and leaves
fields with several distinct arts to the owner. Whether a different art is
better is taste, and ADR-0059 forbids any model output from ordering or
deciding a cover.

Evidence from 2026-10-24 to 2026-10-26, on one owner's 201 labelled cover
considerations. A model wrote a profile from past picks, and a vision model
applied it to new cases with a closed verdict.

| Approach, pre-registered holdout of 57 decisive cases | Matched the owner |
|---|---|
| Profile judge | 86.0% (49 of 57) |
| ADR-0093's deterministic rules | 77.2% |
| Keeping the current cover | 71.9% |
| The owner's stated preferences alone | 64.9% |

- Switch precision was 9 of 11 for the profile judge, against 7 of 21 for
  stated preferences. In cross-validation over 138 cases the judge scored
  75.4% against 63.8% (p = 0.017). What the profile adds is restraint.
- A builder that sees the chosen and passed-over previews, with dimensions,
  shape against the medium and same-art group, matched one given the owner's
  notes (75.9% against 74.9%), so the owner is never asked why.
- A profile from 25 picks performed about as well as one from 50 or 100.
  Rebuilding the same profile moved accuracy by about 4.6 points, the size of
  every effect tested. A detailed profile of about 860 words beat a 300-word one
  (81.2% against 77.4% at art level, p = 0.034) and was adopted by the owner
  despite more switches, after rating 12 of 15 sampled "wrong" switches
  acceptable.
- Hiding the current-cover marker roughly doubled switching; shuffling the
  candidate order did not matter beyond rebuild variance.
- Library replay over 1,603 cover fields after ADR-0093's stage: 1,487 leave
  attention, 106 become one confirm-or-reject proposal, 10 stay open. The judge
  stage cost $0.13.

These figures come from one curated library and may not transfer, which is
why live confirmations are measured on each owner's library.

## Decision

### A model verdict may choose within the deterministic eligible set

One new kind of advice, the taste judge's closed verdict, may choose a cover
from ADR-0093's post-stage eligible set, as a proposal or, under the owner's
automatic setting, as an automatic decision. It never admits a candidate,
crosses identity or widens the set. Reviewer observations remain advisory and
still cannot rank (ADR-0059, #1417).

The judge decides only where ADR-0093 leaves the choice open: a readable,
fault-free current cover beside other eligible arts, which ADR-0093 keeps, and
a field that would otherwise be an open choice for the owner. Where ADR-0093
makes a deterministic switch or fills a known-empty target, its result stands
and the judge is not asked.

A verdict is advisory evidence, persisted and published before it can
influence a proposal or a decision.

### The taste profile

- **Structured data, never instructions.** Four fixed sections of numbered
  single-line rules: what the owner is drawn to, what they avoid, how they
  trade these against resolution and same-art copies, and when to keep or
  switch. It is placed in a delimited data field of the pinned instruction.
- **Bounds.** Each rule passes the owner-entered text rule (ADR-0085) with a
  400-code-point bound. The whole profile is at most 40 rules, 1,200 words and
  8,192 code points. Builder output that fails any bound is refused, never
  truncated or repaired, and the active profile stays unchanged.
- **Versions.** Every version is immutable and append-only, with model or owner
  provenance per rule. A rebuild replaces the active version directly, with no
  held-back scoring gate.
- **Owner edits win, in code.** Edits use the session-, CSRF- and Origin-bound
  command with an optimistic profile revision and create a new version at
  once. Owner-authored rules are copied verbatim into every rebuilt version;
  the builder cannot rewrite or delete them.
- The browser renders rules as text only.

### The builder

- A new closed request kind, created only by the full cover packet parser.
  Per recorded decision it carries the 320×480 previews of the chosen and
  passed-over candidates and these closed facts: decoded dimensions, shape
  against the medium, same-art group reference, chosen or passed over, and the
  current marker. No original, URL, provider title or text, stated dimension,
  title, author, series, identifier, path, service identity, owner reason or
  observation.
- **Input cap.** At most 50 decisions per build: the most recent corrections
  first, up to 25, then a deterministic sample of other decisions. The
  truncation is recorded on the version. Every build is one full request,
  never split into incremental revisions, which measured worse.
- **Cadence.** One build in flight, at most one per 10 new owner cover
  decisions, at most 4 a day.
- The default model is Sonnet at the product's own 320×480 previews.

### Calibration

Taste-driven proposals start only after one guided session of 25 picks in the
cover review section. It is offered as soon as 25 cover fields are
decision-ready: the first 25 to become ready that offer at least 3 distinct
arts after same-art collapse (falling back to 2 if too few exist), with at most
2 from any one series or author. That selection runs in code and is never sent
to a model. Each pick is that book's ordinary owner decision; stopping keeps
the picks made so far.

### Learning from later decisions

Every later owner cover decision feeds the profile, above all rejecting or
changing a cover the judge proposed or chose. Rebuilds are batched and full.
After a rebuild or an owner edit, only pending proposals and pending automatic
decisions are judged again.

### The judge

- A new closed request kind, created only by the full cover packet parser,
  with the bound profile version and the candidates left after ADR-0093's
  stage: at most 13 (the current cover plus 12 alternatives), each as its
  320×480 preview, shuffled per request under opaque references, with the
  current marker. Nothing else from the provider or the library: no original,
  URL, provider title or text, stated dimension, title, author, series,
  identifier, path or service identity.
- **Verdict.** Exactly
  `{decision: keep_current | choose | abstain, candidate: opaque reference | null, confidence: integer 0-100, rules: 1-3 rule numbers}`,
  with no free text and no extra members, in at most 512 output tokens.
- **Admission.** The candidate is one of this request's references and in the
  eligible set; `keep_current` requires the current cover in the set; every
  rule number exists in the bound version. The verdict binds the exact
  consideration, packet, profile-version digest, candidate-set digest,
  instruction, schema and model.
- **Failure.** Any parse, binding or currentness failure leaves the field an
  open choice for the owner. Nothing retries silently; only an explicit,
  bounded retry calls again.
- **Confidence** orders proposals and sets their Strong or Likely label. It is
  never an admission input.
- One request per field per profile version.

### Proposals

A proposal is one confirm-or-reject per field. The covers come first, with the
suggested one marked, and "Suggested because" below them shows the cited
rules' numbers and their text from that profile version, never model prose.
Confirming is an owner decision; keeping the current cover or choosing another
is an owner decision and a correction. Outcomes feed the profile and measure
live switch precision per medium, which is shown to the owner and used to tune
defaults, never as a gate.

### Automatic mode

- **An owner setting**, Suggest only (the default) or Automatic, on the Taste
  profile view; it is the Policy command for this option, session-, CSRF- and
  Origin-bound with an optimistic revision. No measured accuracy floor or
  confirmation rate unlocks or locks it; the live confirmation rate is shown
  beside it as information.
- **Qualification.** Like every automatic cover option, it can decide
  automatically only once the owner-run qualification held by #1418 admits
  it: the builder and judge on the selected live models, a
  smoke with instruction-like text printed on a synthetic cover, the measured
  cost per build and per judged field, and zero false admissions. Until then
  the setting can be chosen, but the judge only proposes.
- **Protection.** It follows ADR-0093's setting. Under `protected`, a
  judge-preferred switch away from a fault-free current cover is a proposal;
  under `unprotected` it may be automatic; under `proposals_only` nothing is.
  Owner-final decisions, earlier owner cover decisions and carried holds are
  never overridden.
- **Guards.** Every switch passes consent, backup readiness, plan validation,
  optimistic concurrency, read-back and audit, and retains the replaced cover
  under ADR-0093. A field whose replaced cover cannot be retained is not
  switched automatically.
- **No daily limit on switches** (owner decision 2026-11-07). The bounds on a
  bad or steered build are: off by default; `protected` switches only covers
  with a detectable issue; every decision binds its profile version, so a
  rebuild or edit makes pending automatic decisions stale and they are judged
  again, never executed under the old version; the judge's durable daily
  request ceiling; and every switch can be reviewed and put back, filtered by
  profile version.
- **Preview before turning it on.** Choosing Automatic first runs covers
  through the profile until it finds 5 switches it would make, stopping after
  at most 100 covers checked. It shows each with the current cover, the new
  cover and the cited rules, and how many covers it checked, with no estimated
  total. Marking one as looking right or keeping the current cover is an
  ordinary owner decision, and keeping is a correction. After an edit or
  rebuild the preview runs again, checking last time's switches first. Nothing
  switches until the owner presses Turn on, and Turn on never waits for the
  preview. Preview calls count against the judge's ceiling and bind the
  profile version.

**Why this keeps the rule that AI confidence alone never justifies a
production write.** Confidence is not an admission input. An automatic write
rests on the owner's standing, revocable instruction for a qualified option;
the deterministic eligible set, every member of which policy already admits;
the protection setting; and every effect guard. The verdict only chooses
within that set.

### Review of automatic decisions

The Switched view of the cover review section lists automatic decisions newest
first, beside the cover each replaced, with the cited rules. Keep and Put back
are one owner decision each, through ADR-0093's mechanism; there are no bulk
actions. The list filters by profile version.

### Reviewer access, privacy and logs

- Builder and judge are two new task mappings in reviewer access over the
  existing contained and OpenRouter transports: no new host, no new credential,
  no write authority. Each is a separate owner opt-in. The stable cover posture
  (#1124) applies to both: exact model, data-collection denial and the token
  ceiling.
- **New disclosure**, recorded in `data-privacy.md`: which covers the owner
  chose and passed over, and profile text the owner may have written, go to
  the reviewer provider selected for these tasks. Titles, authors, identifiers,
  paths and library names do not.
- Profile and rule text, owner decisions, candidate references, verdicts,
  prompts, previews and raw model output never reach a log line, failure
  history, diagnostic, status or support bundle. Failures are closed codes.
  Activity shows the profile version identity, never its text.

### Bounds

| Bound | Value |
|---|---|
| Profile | 4 sections; at most 40 rules; each rule at most 400 code points; at most 1,200 words and 8,192 code points |
| Builder input | at most 50 decisions (corrections first, at most 25); at most 4 previews per decision and 200 in all, within the model's per-request image limit; one request |
| Builder output | closed schema; at most 4,096 tokens, inside the 8,192 cover ceiling |
| Rebuild cadence | one in flight; at most one per 10 new owner cover decisions; at most 4 a day |
| Judge | one request per field per profile version; at most 13 candidates; at most 512 output tokens |
| Daily request ceilings | per task, durable, owner-visible: builder 8 and judge 5,000 by default; counted before dispatch; stop at the ceiling or on an unknown charge |
| Automatic switches | no daily limit |
| Preview | stops at 5 would-be switches or 100 covers checked |
| Profile edit body | at most 32 KiB, closed map, session, CSRF and Origin bound |

## Consequences

### Positive

- The owner's taste becomes something they can read and edit, and most
  remaining cover choices become one tap or none.
- A steered or poor build is contained by the protection default, version
  binding and put-back, without a cap that would stall a large library.

### Negative

- Covers the owner chose and passed over, and their own profile text, go to a
  third-party model provider.
- Under `unprotected` with Automatic, a build that varies by a few points can
  switch fault-free covers the owner would have kept; the remedy is review and
  put-back, not prevention.
- The accuracy figures come from one library.

### Neutral or deferred

- Judging without the current marker in proposal mode, which the evidence
  suggests finds more real switches, is a later tuning decided from live
  confirmations.
- Book facts as builder input (genre, fiction or non-fiction, series) stay
  out; an offline check found no genre-dependent taste, and the judge stays
  genre-blind.
- An opt-in daily limit on automatic switches is a possible later expansion.
- Profiles are never shared between owners.
- Clean rebuild required under ADR-0013: profile versions, verdicts and their
  bindings are new append-only state with immutable-row triggers, with no
  adoption or migration.
- On acceptance, `security.md` (AI reviewer boundary, cover observer,
  credentials and egress, decision authority), `ai-boundary.md`,
  `data-privacy.md`, `configuration.md`, `automation-policy.md`,
  `review-experience.md` and the changelog change in the activation pull
  request.

## Alternatives considered

### Stated preferences alone

They propose many wrong switches (7 of 21 right on the holdout).

### A linear model over observations

It collapses to always keeping the current cover.

### Ask the owner why they chose a cover

A builder that sees the covers needs no reasons, and even an optional prompt
makes each pick slower (owner decision 2026-10-25).

### Unlock Automatic at a measured floor

There is often no single best cover, so a "wrong" choice against a past pick
may still be acceptable; an owner with thousands of covers may prefer some
choices they would have made differently over deciding each by hand (owner
decision 2026-10-25).

### A daily limit of 100 automatic switches by default

The design-stage security review asked for it as a blast-radius bound. The
owner removed it (2026-11-07) and confirmed the remaining bounds above.

### Incremental profile revisions

They cost more at this size and scored 3.6 points lower than a full rebuild.

## Validation

- A fake reviewer transport proves refusal of an invented, repeated,
  out-of-set, extra-member or out-of-range verdict; every profile bound
  refusal; that owner rules survive a rebuild; that a rebuild or edit makes
  pending decisions stale; every automatic-mode guard; the daily ceilings; the
  preview's stop conditions; and put-back through the retained cover.
- A redaction test feeds profile text, rule text, decisions, references,
  verdicts, prompts and model output in and asserts none comes out.
- The owner-run qualification held by #1418 before Automatic can decide.
- Live switch precision per medium, from confirmations and reviewed automatic
  decisions, is shown to the owner.

## Note (builder, #1372 PR 1)

The status stays proposed. The first slice writes and shows the profile and
nothing reads it. Three decisions made while building it amend the text above:

- **Transport (R1).** The builder receives each normalised 320×480 preview as
  a deterministic JPEG at quality 90, flattened on white, derived inside the
  sealed packet and re-derived and compared byte for byte before sending. A
  build request is at most 24 MB and 100 images. The judge keeps PNG, as the
  cover observer does.
- **Either engine (Amendment 1).** The builder is one task mapping that the
  owner points at the contained Codex reviewer or an OpenRouter model, one
  engine at a time with no fallback. Codex receives the same admitted request:
  the pinned instruction, the sealed packet and the same JPEG previews in
  packet order, each re-checked and bound to its digest and mounted read-only
  as `/images/NN.jpg`. A Codex build is one attempt, so one build is one
  counted request. Codex reports no per-request cost, so a Codex reply counts
  as usage reported and a Codex failure ends the build `unavailable` without
  stopping the day; `unknown_charge` stays OpenRouter only.
  A Codex mapping counts as configured, so builds are scheduled, only
  while the Codex profile is provisioned and its sign-in is usable, read from
  the recorded sign-in flow or the installed sign-in files without starting a
  process or calling the network.
- **Closed facts (S5).** Per image: the original's decoded width and height,
  shape `fits` or `wrong_shape` against the medium, an opaque same-art group
  letter, role `chosen` or `passed_over` and the current marker; per decision,
  its medium, `ebook` or `audiobook`. The medium is the one closed value this
  adds to what a model may receive
  ([AI trust boundary](../ai-boundary.md#taste-profile-builder)).

## Note: owner edits (#1372 PR 2)

- **Edits are versions (Q15).** Each owner edit, addition or removal is one
  new append-only version at once, with no draft. An edit version's base is
  always the version before it; it keeps that version's build, model, inputs
  and time, is never a build and sends nothing to a model.
- **Owner rules survive rebuilds (S7).** A rebuild keeps the owner's and
  edited rules word for word, ahead of the model's in each section, and leaves
  out a model rule whose key equals a kept or removed text. The merge runs in
  code against the newest version as the build is written, so an edit landing
  during a build is kept; a merge over a bound ends the build as an unusable
  reply does.
- **The builder sees them as data (S8).** The packet carries the kept rules,
  the removed texts (at most 100 and 16,384 code points) and the budget they
  leave; the pinned instruction says not to repeat, reword or contradict
  them. Owner text is held to the same rule-text rule as model output.
- **Numbers.** A rule keeps its number through edits; an added rule takes the
  next number above the highest, or the lowest free number once that would
  pass 9,999, so numbers may have gaps. A build numbers its rules from 1 again.
- **Body cap.** The edit route takes at most 8 KiB, under the same session,
  CSRF, Origin and build-identity checks as the profile's Try again, and
  answers a version that is no longer newest as `stale`.

## Note: the judge (#1372 PR 4)

The status stays proposed. The judge runs and stores its verdicts, and since
PR 4b a `choose` verdict is a suggestion the owner sees.

- **Either engine.** The judge is its own task mapping, Codex or an OpenRouter
  model, chosen apart from the builder. Each request is one attempt and one
  counted request; Codex receives the same admitted request with its PNGs
  mounted read-only as `/images/NN.png` in packet order.
- **Where it is asked (Q9).** Besides a fault-free kept cover beside another
  art, the judge is asked where the stage would cure a fault of the current
  cover (`wrong_shape`, `below_floor`, `edition_language`, `sharper_copy`,
  `unreadable`). The judge picks an art and the stage picks its copy: each
  other art is sent as its group's representative, and the current art only
  as a resolution fault's cure. A field with fewer than two alternatives, a
  same-art sharper copy alone, a fill and an open choice stay deterministic;
  an open choice with two arts is not reachable once ADR-0093's stage runs.
- **Packet.** The rules of one profile version as data; per cover an opaque,
  per-request reference in shuffled order, the original's decoded size and
  the current marker; the medium. Previews are the normalised PNGs, each
  re-checked against the digest its comparison recorded.
- **Admission.** In order: one JSON object; the closed schema; `choose` names
  a sent alternative; no candidate without `choose`; no `keep_current`
  without a current cover; every rule number in the bound version. Nothing is
  repaired. The current marker is kept and stored on each verdict.
- **Ceiling and cadence.** `review.cover_taste_judge_daily_requests`, 1–50,000
  and 5,000 by default, counted before dispatch. One judgement per runner
  wake, after the builder; a new version is judged once it has stood 120
  seconds, its pending `choose` verdicts first, and a field whose verdict
  kept the current cover or abstained is not judged again. An engine failure
  pauses judging for 10 minutes; a failed field waits for the owner's Try
  again or a new version.
- **Suggestions (PR 4b).** The newest verdict on a current comparison that
  chose a cover, with no owner selection answering the work, is a suggestion:
  Strong at a confidence of 90 or more, otherwise Likely (Q11, Q12). Strength
  orders To decide and never changes Attention (Q14). A verdict under an older
  version is shown with that version's rule texts until the work is judged
  again (Q4); the suggestion carries each rule's section and the version in
  use. Try again asks again for the failed fields under the version in use
  that could still be judged, as a requested attempt the next wake takes up;
  a repeat with nothing failed succeeds, so a lost answer is never stale.
- **Confirmations.** The verdict in force for an owner selection is the
  newest stored in an earlier second (times are whole seconds, so one stored
  in the selection's own second may have come after it). A selection under a
  `choose` counts toward
  "Confirmed {n} of {m}", and toward n when it took that candidate; one total
  is shown, not one per medium. A selection that differs from a `choose` or
  `keep_current` verdict in force is a correction: the builder sends up to 25,
  newest first, ahead of its sample.

## Note: automatic switching (#1372 PR 5)

The status stays proposed. A `choose` verdict can become an automatic
decision once switching by taste is qualified; until #1418 records that
qualification, every verdict stays a suggestion.

- **One identity (K1).** `cover_order:fixed_source_order:taste_replacement`
  covers both a confirmed and an unconfirmed current cover. Its record is
  `None` until the owner-run qualification sets it. The decision binds the
  target state it saw, and the write checks it again.
- **The decision (K2).** `cover-automatic-decision-v6` names the verdict and
  the profile version. A taste decision must name both; any other decision
  names neither. The write reads the stored verdict row, not the decision's
  copy. The row must bind the same comparison, candidate and version, and the
  stage must give the same reason as at admission.
- **Switching (K3).** Owner settings `cover_preferences.taste_switching`
  (`suggest_only` by default, or `automatic`), mirrored as
  `review.cover_taste_switching`. Switching is not part of the cover policy,
  so changing it prepares nothing again. Each runtime generation binds it when
  it is composed. Under Automatic one control, "Only covers with a fault",
  sets the protection: ticked is `protected`, cleared is `unprotected`.
- **Admission order (K4).** The first gate that fails stores nothing, and the
  gates run in this order:
  1. Switching is Automatic.
  2. The record exists.
  3. The stage's admission basis holds.
  4. The verdict is the comparison's newest, under the version in use, and
     chooses a selectable candidate that is not the current cover and has a
     rank and citations.
  5. Either the stage kept a fault-free cover under `unprotected`, or it
     would replace a fault the judge may cure.
  6. The target state is one the identity covers.
  7. There is room to keep the original.
  8. No hold.
  9. The mint, which refuses over an owner's choice.
- **Retirement (K6).** Each wake of lane 1 first revokes an unwritten taste
  switch (up to 50) whose version is no longer the one in use, or every
  unwritten switch while Switching is Suggest only. A write that started
  stays. An unwritten switch under an older version no longer answers its
  comparison, so the judge judges it again first. Lane 1 then runs build,
  then switching, then judge.
- **Preview (K7).** A read over stored verdicts only. Under the version in
  use it walks each current comparison's newest verdict, oldest first, through
  `cover_taste_verdicts_version`. Each verdict is one checked cover, up to 100
  in all. A switch is a `choose` that meets gates 4-6 under the protection
  asked about. The walk stops at the fifth switch. Posture, record, room and
  holds are not asked. An owner answer since the verdict shows as Looks right
  (the same candidate) or Keep current; any other answer takes the switch out.
- **Looks right (K8)** is the work's ordinary `accept` of that candidate,
  and Keep current its `keep_current`.
- **Cover Resume (K9).** See ADR-0085, Amendment 3. Taste alone never offers
  Resume, because the judge never looks at a cover the owner chose.
- **Cost (C4).** Changing the protection gives every work a new comparison,
  and a new comparison is judged again. One change can spend up to a library's
  worth of judge requests against the daily ceiling. The preview stays honest
  because it reads only what has been judged.
- **What the owner sees.** Switched names the version and the cited rules as
  that version holds them. Activity names only the version.
