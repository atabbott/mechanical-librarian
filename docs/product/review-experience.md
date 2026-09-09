# Review Experience

## Purpose

Mechanical Librarian keeps routine, qualified metadata work out of the active
human queue. When a person is needed, the product presents one durable,
target-shaped decision surface backed by server-authoritative state.

Terminal preparation failures remain visible in Library as noninteractive rows.

## Guided first run

A new empty store uses one calm six-step ledger: set owner password, connect, providers,
libraries, mounts, and kickoff. Completed steps collapse to recorded rows with
Change; reopening a row removes only dependent answers and secrets, and resume
always returns to the first incomplete step. Provider setup is optional,
library discovery remains retryable and summarizes filtered-out podcast libraries,
and mount comparison reports matched, ambiguous, missing, or unavailable
without creating a mount.

Kickoff says exactly: `Nothing in your library changes. Writes stay off until
you turn them on in Activity.` The setup target swaps to the normal authenticated
workspace on the same listener, starts preparation immediately, and offers
`Open workspace`; there is no restart or setup write authority.
As soon as one work is ready, a focused handover card appears above the live,
bounded preparation list. The terminal first-boot measurement appears only in
Library and counts every unique checked work, including terminal failures.

Each persisted failure code maps to one closed safe reason and exactly one
owner posture: a named supported action, a stated pending automatic recovery,
or an explicit statement that no owner action is available. Unknown codes are
rejected from the projection rather than receiving invented guidance. The row
shows only its closed stage, safe reason, owner posture, and copyable bounded
reference; no retry, case detail, item path, or provider payload is exposed.

This document is the product contract for that experience. It incorporates the
validated behavior from the retired prototype baseline, while rejecting the
prototype's static-file delivery and browser-local workflow authority.
Locked variant M at `026e338` is the authoritative visual contract for the
in-scope Library, Attention, and Detail surfaces. Its prototype code remains
directional evidence rather than production code. Its separate library
activity view is not an F16a surface: F17 adds Activity as one permanent,
badge-free destination for guarded readiness, consent, a quiet ledger and
grouped recovery. Library-wide
bibliographic or cross-case history remains deferred to #195. Variant I is
retained only as design lineage.

## Governing outcomes

- New items remain available while curation runs asynchronously.
- Qualified non-destructive work can resolve and apply without human action.
- The active queue contains only work that can benefit from an owner decision.
- One ordinary review case represents one adapter-scoped target snapshot.
- Decisions stay independent per target and per field.
- Automatic approvals and rejections remain visible in current-case history
  without crowding the active queue. Cross-case history search belongs to
  post-first-release outcome #195.
- Destructive work remains separate and always requires explicit authority.
- A browser refresh, device change, restart, or upgrade cannot lose an
  acknowledged decision.

The [automation policy](automation-policy.md) is authoritative for which work
may be automatic. This document governs how unresolved and historical work is
represented and operated.

## Review identity

An ordinary review case is identified from:

- the configured adapter and library connection;
- the stable internal target identity;
- the observed item snapshot and its fingerprint; and
- the proposal-set version presented for decision.

A new target fingerprint or materially changed proposal set supersedes an
unresolved case rather than mutating it in place. Historical cases and
decisions remain immutable.

The #449 service retains exact ordinary-field `accept`, `keep_current`, or
historical `reject` facts. New Library choices are current-value `keep_current`
or exact eligible `accept`; it binds the current case, target fingerprint, field proposal,
owner actor, selected value where applicable, and idempotency key. The decision
and its current-case audit event are append-only and atomic; an acknowledged
decision removes only that exact field from current attention. Browser decision
controls and advancement remain the #450 activation increment.

The same external item value in two configured libraries is not assumed to be
the same target. Titles and authors affect sorting and discovery, never target
identity.

## Review cases and groups

An ordinary `ReviewCase` contains only the decision surface for one target:

- current metadata and cover state, including explicit missing or unloaded
  states;
- independently selectable field proposals;
- typed cover and series choice groups;
- evidence and notice references;
- evidence-derived recommendations and deterministic policy disposition;
- the current applicable human decision, if any; and
- apply eligibility and stale-state information.

Evidence-only observations enrich a case and never create empty actionable
queue rows. Identical candidate values collapse into one choice while retaining
every source assessment, evidence lineage, source-specific problem fact, and
contradiction. The displayed aggregate is the current deterministic policy
result, not a union of historical risks or the verdict of one strongest row.

The Library decision canvas projects that retained lineage onto each current or
proposed value: local presence, named external corroboration, eligibility
rationale, recommendation status, and any material limitation stay attached to
the choice. `Recommended` appears only for an authoritative deterministic
selection; otherwise the canvas explicitly records that there is no
recommendation. Library is the only per-work decision workspace and owns the
comparison and its inline disclosure. Evidence and History remain read-only
per-work diagnostic pages: they expose retained evidence and current-case
history without decision controls, link to each other, and provide Back to work
for the exact selected Library work, field, and lens. Their bibliographic
identity uses the retained readable title, bounded authors, library, and media
format rather than an internal item identifier. Evidence presents bounded
attributable observations in field order, with source identity beside each value
and provider outcomes in a subordinate Sources disclosure; missing-lineage
limitations never claim a field is empty while more evidence remains. History
describes durable owner and automatic outcomes and
shows values only when the retained contract carries an exact authoritative
link. Neither page exposes raw retained facts or internal event, decision,
candidate, report, target, consideration, or reference identifiers. They have
no legacy redirect or compatibility behavior. Durable cross-work diagnostics
and history remain in Activity.

Evidence and History preserve the originating Library lens, bounded search,
Library page cursor, Attention cursor and selected field through tab switches,
refresh and Back to work. Return links use the case identity in the diagnostic
path, including while diagnostics load or cannot be read. Unknown or malformed
query values never become return settings; returning does not independently
fetch an off-page work to disguise lost Library context.

A `ReviewGroup` is a display/query projection for confirmed instances of the
same work, such as an Audiobookshelf ebook and audiobook or a future target in
another adapter. It may contain identity labels, link method, link confidence,
and ordered sibling case IDs. It never owns selections, decisions, readiness,
or apply state. Reviewing one sibling cannot alter another.

Title and author similarity may keep likely siblings adjacent when no confirmed
link exists, but the interface must not present proximity as a confirmed group.

Duplicate and removal proposals use a separate destructive review case. They
cannot be embedded in an ordinary case, automatically authorized, or included
in ordinary bulk decisions.

## Field choices and evidence

Every proposed field change distinguishes:

- current value and whether it was loaded;
- recommended choice;
- materially different alternatives;
- explicit keep-current or no-change behavior;
- source consensus;
- supporting and contradicting evidence IDs;
- deterministic recommendation and reason;
- the bounded `problems` list containing only adverse or unresolved
  facts, with positive support shown through the cited evidence and reason;
  the list may be empty when there is no adverse fact, including a manual
  recommendation that is fail-closed for insufficient or conflicting support;
- applicable risks and notices; and
- whether the choice is allowed for the target adapter and media type.

An uncertain field cannot suppress a separately safe field on the same target.
Rejected, inapplicable, disproven, and no-op alternatives remain auditable but
cannot contribute to an apply plan.

Cover recommendations require recorded identity and visual-quality evidence.
Series choices preserve membership and sequence independently. Exact regional
cover matching is not required when the selected image is otherwise correct
and clearly more useful, as defined by the
[automation policy](automation-policy.md#cover-policy).

Evidence is stored once as an immutable bounded record and referenced by ID.
List responses return summaries; detailed facts and bounded snippets are loaded
only when requested. Raw production paths, unrestricted source payloads, and
copyrighted content are never exposed through ordinary review endpoints.

## Notices

Reviewer-facing notices use four classes:

- `blocking`: the selected or proposed action cannot safely proceed;
- `needs_attention`: useful unresolved context for a human decision;
- `resolved`: a prior concern was evaluated and no longer blocks work; and
- `rejected_alternative`: audit context attached to a choice that cannot be
  selected.

Only selected choices, canonical readiness failures, stale state, and explicit
policy constraints may block a decision or apply plan. Operational failures
such as provider outages or job retries belong in health and job views unless
they create a genuine metadata choice.

## Decision and authorization separation

The product represents these facts separately:

1. Candidate and evidence facts.
2. Evidence-derived deterministic recommendation.
3. Deterministic policy disposition.
4. Human decision, when required.
5. Apply-plan authorization and readiness.
6. Apply, verification, and rollback outcomes.

A recommendation is never itself production authority. A qualified Automatic
decision requires separate immutable policy qualification bound to the exact
review case, policy version, and deterministic rule codes; disposition alone is
never authority. That decision-only outcome still requires the ordinary exact
effect authority, consent, backup, currentness, and verification gates before a
plan may write. An admitted Ask case requires an applicable human decision
before an apply plan can be built. Destructive work always requires typed
explicit authorization.

Delivered F10b admission requires a complete target-shaped ProposalSet
and preserves it as one immutable undecided case; it does not introduce a
decision surface, reviewer identity, queue, or write operation. It requires at
least one `propose_change`, `acceptable_choice`, or `unresolved_choice`.

Human actions are explicit and field-aware:

- accept the selected changes;
- keep current values or reject the presented proposals;
- defer without pretending the case is resolved; or
- provide the typed authorization required by a separate destructive flow.

`Blocked` is not a human decision. Unsupported capability, stale state, missing
evidence, policy restriction, and operational failure retain their own typed
states.

## Queue, detail, and summary

The authenticated Library is the complete work surface. It opens on the `all`
lens with server-authoritative attention counts, presents readable title,
author, library, and media identity, and keeps works needing a human decision
first without hiding automatic-only, resolved, pre-admission, current no-case,
or retired work. Within the attention group it presents works whose current
provider report is complete before those with a provider-limited lane; both the
`all` and `needs_attention` lenses keep that grouping while
`no_attention_needed` remains alphabetical. Page-local headings name
**Provider evidence complete** and **Provider evidence limited**, preserving the
server order even when a page begins partway through a group. Clear works end
the attention grouping. Compact row descriptions name the observed lookup
statuses. Complete includes `no_match`, disabled, and insufficient-input lanes;
it does not promise a match. Limited is broader than retrying: a failure status
alone never promises a scheduled retry. Recoverable lookups can change group
when new evidence arrives. Graded ordering remains deferred to #877, including
the original ≥10% steady-state criterion; the earlier measurement is unavailable.

An unresolved Automatic field explains withheld decisions only when its exact
current policy/report/proposal preview identifies failed contributing provider
evidence. The explanation stays beside that field in Library and Evidence;
unrelated failed lookups create no causal claim. Existing direct choices remain
available, and replacement evidence removes an explanation whose cause no longer
holds. A current work without a review
case remains readable and searchable but does not offer a false detail or
preview action. Internal
connection and item keys do not become its primary visible identity. Search
covers bounded title, author, library, item, and media values with literal
wildcard handling. The
supported needs-attention and no-attention lenses, bounded search, cursor
pagination, selected-work preview, deep links, and browser history remain
URL-owned. Library lenses, title links, preview actions, and pagination remain
44px touch-safe at desktop and compact widths. Long and unbroken work titles
wrap within a bounded two-line title area so distinguishing text remains
readable without widening the work list or document.

Attention is a contextual, URL-owned field queue inside the regular-width
Library workspace, not a peer navigation destination. It contains only
unresolved ordinary fields that can benefit from an owner decision, groups them
by readable work identity, and keeps the current work, active field, and queue
position visible beside the decision canvas. At narrower regular widths the
work index and selected detail remain concurrent while attention opens as a
contextual drawer. At phone widths the queue may become a separate
single-column step while preserving Back/Forward state and task continuity.
Every responsive composition retains the attention-only field strip's current
value and stable position, inspectable automatic/recorded/no-change siblings,
the external-library capability summary, and the source/observation/basis
ledger. A field keeps its original actionable position after an earlier field
settles, and retained case provenance remains visible when supplemental
evidence cannot be loaded. Its work-level field count remains complete when cursor
pagination splits a work, and evidence context counts bounded records rather
than claiming distinct providers. Decision preview copy promises only the
current value and available choices until the exact case supplies its eligible
direct-value choices. An empty queue does not imply an empty Library:
automatic-only and resolved works remain discoverable there. Destructive work
remains outside this ordinary queue.

The queue supports cursor pagination, stable ordering, deterministic
tie-breaking, title/author/target search, and explicit filters for:

- review state;
- configured library or media type;
- proposed change type;
- notice severity; and
- advanced source/provider diagnostics.

The default order prioritizes unresolved human work while keeping confirmed
siblings adjacent. Filtering cannot leave a hidden case active.

The server admits authenticated client entry and refresh for every supported
review path without treating the URL segment itself as data authority. The
review API remains the authoritative existence and shape check, so a missing or
invalid case renders the application's bounded recovery state rather than a raw
server error. Root entry redirects to sign-in.

The detail view presents identity and current-versus-proposed choices before
supporting evidence. The current value and each specific eligible proposed
value are themselves the mutually exclusive direct choices: selecting one
records the existing exact-field decision immediately, without generic
Accept/Keep/Reject controls or a separate submit step. Irrelevant panels are
absent. Evidence explanations, source rows, policy versions, and diagnostics
are progressively disclosed.

The embedded Library decision surface follows the same command semantics as
full review. An ordinary failed command may expose one explicit same-decision
retry using the exact preserved command and idempotency key; mutations are
never retried automatically. An ambiguous or unreconciled outcome remains
locked until a later authoritative current-case read proves a safe state.

Every field exposes one closed server-owned interaction classification. Owner
attention and automatic abstention are queued; an ordinary automatic outcome is
inspectable and may be deliberately overridden but is not ordinary next work.
Recorded owner outcomes remain inspectable. Unsupported or suspended fields
retain their current value and a specific explanation, never a selectable
``Not set`` placeholder. Equal current and proposed values render as one
full-width preservation choice.

## Protected Library workflow

The protected Library workflow keeps the work index, stable selected-work
identity, full-width field rail, decision/evidence surface, and completion
boundary in one responsive route. On wide entry it selects the first
server-ordered reviewable work without recording a decision; compact entry is
list-first. The rail preserves its ordered fields after a recorded decision,
allows direct revisiting, compresses labels before scrolling, and is never
replaced by a competing review back control.

Current values and exact eligible changes are the commands. Pointer selection
may show recording and recorded acknowledgement before authoritative
advancement, but ambiguous, stale, failed, and reconciliation states never
advance or claim success. A completed work lists each field and recorded
outcome, retains work identity, and offers deliberate next-work continuation.
It does not imply external application or verification.

An audiobook is reviewed through the same rail as an ebook, with Narrators and
ASIN among its ordered fields, and its decisions apply through the same guarded
path
([ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).
Narrators is a written field on both media. A decision retained from before
audiobook writes existed still reads as recorded and not applied, in those
words beside the decision it describes. A prepared audiobook that produced no
case at all is not a failure: the library counts it as recorded, and its row
opens the evidence its report retained
([ADR-0069](../architecture/decisions/0069-audiobook-review-cases-and-terminal-outcomes.md)).

Cover review remains advisory: small eligible sets compare current and
candidates equally; larger sets retain the incumbent while exposing the full
eligible gallery. Below 900px the incumbent becomes the first item of the same
horizontally scrollable comparison track as every candidate, so covers stay in
one row rather than stacking the current cover above the alternatives. Every
image is contained whole at its intrinsic aspect ratio inside a quiet 5:8
frame; unused frame area is the choice card's own surface, so letterboxing
cannot be mistaken for cover content. Excluded candidates remain subordinate
with their recorded reason. Reduced motion preserves the same status text,
success state, focus, and polite announcement without nonessential movement.

Each cover candidate keeps visual advice subordinate to the whole-cover choice.
Its always-visible check summary distinguishes explicitly clean defect evidence,
one or more named defect flags, uncertain checks, and unavailable reviewer
evidence. The exact defect values precede the quieter composition, medium,
branding, imagery, title-language, series-style, and thumbnail-legibility notes
inside one disclosure. Source provenance remains in the adjacent evidence
disclosure. None of these observations confirms a match, changes deterministic
ordering, or earns automatic authority.

An unresolved field with no eligible alternative remains readable context, not
owner work. Only an eligible alternative makes retaining the current value a
meaningful direct choice; automatic, resolved, and stale fields never present a
false choice.

A decision advances only after its server acknowledgement and authoritative
exact-case read confirm the recorded field. Library counts and
attention totals converge afterwards and are never waited on, so next-field
authority never depends on a whole-Library projection. A field whose retained
attention context has no admitted action remains readable but noninteractive;
a duplicate choice for a field
already settled becomes no second command. If no other unresolved field
remains, the recorded field stays as the terminal case. Deliberate navigation
away from that field while the refresh is pending is preserved rather than
overwritten by automatic advancement. A recorded field leaves Attention but,
when its exact current case still admits eligible replacements, direct
revisiting restores those choices with the recorded choice marked; a later
choice appends another immutable decision. Claimed, applying and reconciling
effects remain temporarily non-replaceable, as does a verified cover effect;
a verified reversible scalar effect may be replaced through the same current
case's guarded correction path.

Recorded human decisions retain their exact selected value in the detail view.
The deterministic disposition remains distinct from that live decision record.

The post-first-release #195 outcome may add a global review summary presenting
remaining work, conflicts, stale cases, readiness, and planned changes by
adapter. It may also expose automatic cross-case history through separate
filters and sampling views rather than mixing it into the active queue. These
cross-case summary and history surfaces are not F16a or first-release
acceptance.

Summary counts are server-authoritative and name their unit:

- `target_count` is the number of distinct adapter-scoped `TargetId` identities
  in the selected scope; the current snapshot and fingerprint are version
  bindings and are counted separately where snapshot totals are exposed;
- `ordinary_case_count` and `destructive_case_count` count their separate case
  classes;
- `field_work_item_count` counts independently decidable ordinary field, cover,
  or series actions; destructive actions remain represented only by their
  separate destructive-case count; and
- `planned_change_count` counts validated changes in current apply-plan
  versions, not proposals or recommendations.

One target may therefore contribute several work items and planned changes.
Every summary includes the selected scope and version, calculation timestamp,
and completeness state. A partial result identifies missing partitions; a
stale result identifies the superseded input version. Neither may be relabelled
as a complete current total, readiness, or successful completion.

Evidence readiness counts unique evidence records and target scopes. It
preserves current, missing, stale, unsupported, and policy-approved-skip states
rather than collapsing them into one boolean. Approved skip records the exact
policy version and rule. The projection carries evidence references only; facts
remain in bounded evidence detail and deterministic packet schemas.

## Bulk decisions

Bulk decisions and their reversal flow belong to post-first-release outcome
#195. They are retained here as a future contract and are not F16a or
first-release acceptance.

Bulk review is an optimization for a bounded unresolved queue, not an
alternative automatic policy engine.

A bulk operation has two server-side phases:

1. Preview evaluates the filtered scope against exact case and proposal
   versions and reports eligible cases, selected fields, adapter totals, and
   every exclusion reason.
2. Commit requires the preview identity, an idempotency key, and unchanged case
   versions. It creates individual attributable decisions plus one batch audit
   record.

Duplicates, removals, stale cases, blocking selections, incompatible fields,
and visually unresolved covers are never bulk eligible.

Reversing unapplied decisions creates compensating decision events. Once apply
begins, recovery uses the apply and rollback contracts instead of a user-
interface undo action.

## Settings route

`/settings` is a lazy authenticated primary destination alongside Library,
Policy, and Activity. It groups Connection, Providers,
Libraries, Automation & limits, Reviewer, Server, and Appearance without
moving cover-policy ownership out of Policy. The selected group is the closed
`?group=` URL state, so Back and Forward restore it. At 884 pixels and above a
settings index remains beside the active paper-like page; at 390 and 320 pixels
a labelled native selector replaces that index.

Successful store-managed changes are confirmed once at page level as saved and
in use. Only deployment-managed fields carry a per-field deployment consequence.
Loading, retry, optional empty, validation, preparation, save
pending/saved/failed/stale, secret add/replace/confirmed clear, and reconnect
states are explicit. In file mode the page names
`/data/config.json` and renders no covered edit affordance. Secret values never
render; only `Configured · ····xxxx` or `Not configured` appears.
Disabling a provider keeps its configured write-only value available to replace
or clear, removes its library associations, and never restores those
associations when the provider is enabled again.

Copy must orient the owner, explain a real consequence or limitation, resolve
ambiguity or status, or give a useful next or recovery action. Otherwise it is
omitted; human, concise, non-technical wording is the default.

Controls retain 44-pixel targets, visible focus and semantic status announcements; long
owner values wrap without document overflow at 320 pixels and 200% text.

## Responsive and accessible behavior

F16a qualifies the individual queue, case-detail, evidence, and decision round
trip against the first-release responsive and accessibility baseline below in
the primary supported browser. Outcome #195 owns additional browser and
viewport coverage and extended accessibility and responsive qualification; it
does not defer or replace this baseline.

- Narrow screens use separate queue and case surfaces rather than stacking the
  entire queue before the selected detail. When #195 adds a cross-case summary,
  it receives its own narrow-screen surface.
- The interface has no document-level horizontal overflow at a 390-pixel
  viewport.
- Primary touch targets are at least 44 pixels.
- Case changes reset detail scroll intentionally while preserving queue
  position.
- Back/Forward navigation, filters, empty results, and previous/next unresolved
  navigation have deterministic behavior.
- Native semantic controls, headings, fieldsets, legends, visible focus,
  accessible names, and polite status announcements are required.
- Color is never the only representation of status.
- Keyboard shortcuts are discoverable, optional, and disabled while editing or
  using an assistive text control.

## Performance and privacy budgets

The production interface does not embed a full-library dataset into one static
document. Collection endpoints are paginated, case detail and evidence are
loaded on demand, and covers outside the active context are lazy-loaded.

F16a defines measurable first-release budgets for its individual queue,
case-detail, evidence, and decision round trip:

- JavaScript and stylesheet assets;
- initial API response bytes;
- evidence-detail response bytes;
- initial DOM nodes and rendered queue rows;
- cover decode work; and
- interaction latency on the supported narrow viewport.

Performance optimization cannot omit decision provenance, evidence references,
validation, or stale-state guards.

For the same F16a baseline, authenticated review responses are private and
cannot be stored in shared caches; browser storage holds only non-authoritative
display preferences. A restrictive content-security policy and text-safe
rendering prevent hostile metadata, provider content, URLs, or evidence from
becoming executable markup. Synthetic browser artifacts must be bounded,
sanitized, and free of credentials or private library data. Real-browser
qualification runs these privacy, storage, content-security, hostile-rendering,
performance, and artifact checks in the primary supported browser.

The production presentation retains variant M's branded editorial shell,
display/UI typography pairing, complete light/dark palettes, work identity and
status language, attention-only field navigation, current-to-outcome decision
hierarchy, progressive evidence and current-case history, interaction states,
and compact navigation. Qualification covers 1440, 884, 390, and 320 CSS-pixel
widths in both themes without document-level horizontal overflow. The selected
theme is non-authoritative presentation state and cannot affect review data or
decision authority.

Activity preserves the Library workspace and Policy hierarchy. Once durable
activity exists, its current guarded state, shared recovery, and recent outcomes
lead the page; write-permission inventory remains reachable from that state and
collapses to a concise summary for returning owners. Before any activity or
consent it opens that disclosure to show prepared human/automatic counts, all
14 destinations with exact included/excluded readiness, one mutation slot, a
representative before/after example, backup readiness and one enable action. A
partially consented library returns to the same disclosure when a newly eligible
destination can be added; the action binds the exact visible subset and fails
safely if it changes. Its collapsed ready count uses the same backup,
capability, unconsented-destination, and prepared-work gates as the enabled
checkbox and command; unavailable action is never summarized as readiness.
Qualified automatic outcomes are named clearly, unknown
origins remain neutral bounded text, and pause/resume/revoke remain the only
prominent controls. Activity does not expose internal jobs or rates, add a
badge, or turn operational failure into a metadata decision.

Every Activity record reads in one order: the work or setting, the field, the
exact change, and the current result. A metadata or Policy change names the
exact before and after, who or what chose it, and whether Audiobookshelf
confirmed it; a Policy record additionally lists each setting it moved with
readable From and To values. A record that was not a change -- a paused or
closed preparation, a recovery, a cover safeguard -- keeps the compact detail
density and says what happened instead of being given empty previous and new
fields. Categories are the owner's four: owner choice, automatic choice, policy
change, and recovery, so a verified write is never labelled a review because
its authority happened to be human. Every value comes from the event's own
immutable decision, plan or verification document, admitted by that document's
own fingerprint, so a later catalogue change cannot alter a historical record.

The default Activity ledger shows one row for each immutable change identity;
the latest durable transition supplies its result and time. Its bounded
inspection discloses the ordered lifecycle without replacing, rewriting, or
loading the complete append-only audit stream. Search, filters, date bounds,
totals, and pagination operate on those rows server-side.

The delivered F16a detail page uses a bounded horizontally scrollable field
attention-only strip inside a non-overflowing document. For each genuinely
unresolved field, the current value and every specific eligible proposed value
are the immediate decision controls; selecting one records that value without
generic Accept/Keep/Reject controls or a redundant submit step. A server
acknowledgement reloads authoritative detail/history, preserves the strip
position, announces a non-obscuring status, and focuses the next unresolved
field. A failure preserves the original choice and idempotency key,
does not advance, and focuses an explicit retry. A current-case conflict reloads
authoritative detail/history instead of re-enabling stale controls, while a
superseded case routes back to the authoritative Library. If a response is lost
or cannot be validated, the browser makes one bounded reconciliation attempt
with the exact same request and idempotency key; any unsuccessful second result
reloads authoritative state or requires sign in without describing the decision
as unrecorded.
Automatic-only fields are collapsed read-only context outside the strip and
never expose those controls. The supported detail response contains no model
advice or contained-reviewer result.

Outcome #195 extends those budgets and checks across its cross-case summaries,
history, bulk and reversal flows, and additional browser and viewport matrix.

## Explicitly unsupported prototype behavior

Mechanical Librarian does not carry forward:

- generated static dashboard HTML as the application;
- browser `localStorage` as authoritative decision state;
- whole-library payloads embedded into a page;
- browser-generated JSONL as apply authorization;
- run-file or source-line identity as workflow identity;
- CWA/Audiobookshelf names hard-coded into the core domain;
- automatic migration of prototype dashboard state; or
- blanket human approval for work that qualified deterministic policy can
  resolve safely.

Local storage may hold non-authoritative display preferences only. Optional
downloads are audit or interoperability artifacts, not workflow state.

## Release acceptance

The review experience is ready when:

- one target snapshot produces at most one ordinary active case;
- a new fingerprint supersedes stale unresolved work;
- sibling decisions cannot leak across targets;
- initial UI state never invents a human decision;
- automatic and human authorization paths produce equivalent immutable apply
  provenance;
- unsupported, rejected, stale, conflicting, and destructive work cannot enter
  an ordinary apply plan;
- the F16a individual queue, detail, evidence, and decision round trip passes
  the first-release responsive, keyboard/focus, accessibility, private caching
  and storage, content-security, hostile-rendering, performance, synthetic-
  artifact, and primary-supported-browser baseline in real browser tests that
  derive categories from the versioned product and category-capability
  contract, exercise at least one representative fixture for each applicable
  category, record explicit reasons for not-applicable categories, and fail on
  unexpected page or console errors;
- untouched presentation defaults remain bound to the current proposal version
  and may refresh only when that version changes, while an acknowledged user
  decision is never silently rewritten by a new default; and
- restart and cross-device tests prove that acknowledged state is server-
  authoritative.

Cross-case summaries and history, bulk decisions and reversal, additional
browser and viewport coverage, and extended qualification budgets remain
post-first-release work under #195. They are not prerequisites for this
first-release acceptance.
