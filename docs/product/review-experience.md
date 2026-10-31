# Review experience

This is the product contract for the owner workspace: first run, Library,
Attention, Evidence and History, Policy, Activity and Settings.
[PRODUCT.md](../../PRODUCT.md) sets the boundaries, and the
[automation policy](automation-policy.md) decides which work may be automatic.

## Principles

- Items stay available in Audiobookshelf while curation runs in the background.
- Qualified non-destructive work resolves without the owner. Attention holds
  only work that can benefit from an owner decision.
- Decisions are independent per target and per field.
- Review makes no destructive changes; see [PRODUCT.md](../../PRODUCT.md).
- The server holds every decision. A refresh, another device, a restart or an
  upgrade cannot lose an acknowledged one. Browser storage holds display
  preferences only.

## Cases and authority

A review case covers one target snapshot: the library connection, the internal
target identity, the item's fingerprint and the proposal set
([review cases](../operations/review-case.md)). A new fingerprint or a
materially changed proposal set supersedes an unresolved case. Recorded cases
and decisions never change. The same item in two libraries is two targets, and
titles and authors never decide identity.

A case holds the current metadata and cover (with explicit missing or unloaded
states), field proposals, cover and series choices, evidence references, the
deterministic recommendation and disposition, and the current owner decision.
Identical candidate values collapse into one choice that keeps every source and
contradiction. Evidence alone never creates a queue row. An uncertain field
never holds back a safe field on the same target. Rejected and no-op
alternatives stay auditable but are never written.

An ebook and its audiobook are separate works with separate cases. Deciding
one never changes the other, and similar titles are never treated as the same
work.

These facts stay separate: evidence, the recommendation, the policy
disposition, the owner decision, write authority, and the write with its
verification. A recommendation is never authority. An Automatic decision needs
its own qualification bound to the exact case and policy version
([ADR-0036](../architecture/decisions/0036-qualified-decision-only-automatic-policy.md)).
Every write, owner or automatic, also needs the library's write permission,
current source and target state and a ready backup, and is verified by
read-back afterwards ([ADR-0037](../architecture/decisions/0037-origin-neutral-guarded-field-effects.md)).

The owner can choose the current value or an eligible proposed value, enter a
value where none fits, withdraw an unwritten decision, choose again a value a
write replaced, or leave a field undecided. Blocked is a state, not a decision.
Provider outages and job retries appear as preparation status, never as a
metadata choice.

## First run

A new store opens a seven-step setup: owner access, Audiobookshelf connection,
providers, libraries, mounts, an optional cover reviewer, and kickoff.
[Deployment](../operations/deployment.md) covers the claim token and container.

- Each step states its rules first. A refusal sits beside its field, clears
  when the field changes, and gives a safe cause and one remedy, never a code;
  at 200% text it stays in view.
- Completed steps collapse to a row with Change. The step list stays beside
  the step while it fits the window and scrolls inside itself when it does not. Reopening removes only the
  answers and secrets that depend on it. Resume returns to the first incomplete
  step, and a change in another tab restores the latest recorded step.
- A connection failure names its class (name not resolved, refused,
  certificate not verified, no answer, key not accepted), the origin and one
  remedy. An address with a path, user name, password, query or fragment is
  refused before any request.
- Providers are grouped as Books and ebooks, and Audiobooks, and are optional.
  Once libraries are read, a group is suggested for those it fits. Nothing is
  ticked for the owner.
- Library discovery can be retried and summarises skipped podcast libraries.
  Two libraries cannot share a name in any letter case. A library's display
  name is a label: renaming it never changes an identity.
- A mount comparison reports matched, ambiguous, missing or unavailable, with a
  remedy only when failing. It never creates a mount.
- The reviewer choices are equal and name the model and its status. Device
  sign-in can be cancelled, resumed or retried. Description advice is set up
  later in Settings.
- Secrets live only in their inputs, with an accessible reveal control; the
  reviewer key is cleared after each attempt. An expired session keeps every
  recorded step.

Before kickoff a summary lists each answer, and each row reopens its step.
Kickoff says exactly: `Nothing in your library changes. Writes stay off until
you turn them on in Activity.` The same listener then serves the workspace,
preparation starts, and a handover card appears once one work is ready.

## Workspace shell

The destinations are Library (with Attention and work detail), Policy, Activity
and Settings. Every page opens in the saved theme, or the system theme until
one is saved, on its first frame.

Sign-in has Show password. It says whether the password was wrong or the device
must wait five minutes after too many attempts. There is no reset in the app;
see [Forgotten owner password](../operations/deployment.md#forgotten-owner-password).
Signing in returns to the page you opened, or to Library, and opening the
address while signed in goes straight to Library.

The masthead holds still while the live-update and preparation status change.
It is sticky only while it takes at most a quarter of the window; taller, it
scrolls away, so it never covers content or focus. The current destination is
marked, and Library stays current while a review or Attention is open.

Navigation mounts without waiting for data, and loading reads as one small
line beside a dot that pulses only when motion is welcome. A pending or failed
count is never shown as zero, and a failed read keeps a retry. An older
response never replaces a newer selection. While a newer read is pending, the last content
stays, marked busy with its controls inert, and the route is announced only
when the read settles. Owner actions take priority over background refreshes.
While preparation publishes works, an active tab refreshes Library and
Attention at most once every 1.5 seconds. After 10 seconds without input, a tab
refreshes only the preparation panel; it reads Library again on the owner's
next input or when the tab returns to the foreground. An unknown review path
shows a recovery state, not an error.

## Preparation and provider status

The preparation panel states one run in four counts: ready to browse, recorded
with nothing to decide, could not finish, and still in progress. Ready to
browse is always shown; the others are hidden at zero, and an unknown count
reads `…`. Counts describe the run's works as they stand now. Each stage bar
counts books that first reached it in this run; a book that could not finish
stops at its last stage. Recent completions record each book's first finish.
A rate appears only while the run is running. Each book links into Library by
the name shown.

The masthead's background-work control shows each library's scan state and
counts, and marks a library outside the current run. At idle it reads
`Preparation idle` and still offers Scan now and Pause. Pause stops new scans;
deferred provider lookups keep their schedule. The popover fits the window
below the masthead, scrolling inside itself when it must. `Escape` closes it and
returns focus to its trigger. Pending commands are disabled, not removed, so
focus stays put and a second press sends nothing.

Retry-shaped controls differ:

- `Scan now` and `Retry` start a new preparation cycle for one library.
- `Retry now` retries every deferred provider lookup in every library.
- Retrying a cover or description consideration asks the reviewer about that
  one item again.

Limited lookups recover on their own schedule; a failure before a report has
no automatic retry ([provider recovery](../operations/provider-recovery.md)).

Library and Settings share one provider notice. Each entry names a source, a
library, the reason, how many lookups wait and when they retry. A refused key
waits for Retry now, and an exhausted lane says no retry remains. Each entry
links to Settings › Metadata sources. Retry now's outcome is announced beside
it; a file-managed deployment shows the file instruction instead. With nothing
deferred the notice takes no space.

A work whose preparation could not finish stays in Library as a
non-interactive row, Preparation failed safely, with its title while the
catalogue still matches. Each failure code maps to one safe reason and one
posture: an owner action, a pending automatic recovery, or no action. Unknown
codes are rejected, not given invented guidance. The row shows its stage,
reason, posture and a copyable reference, never a path or provider payload.
If its catalogue identity has changed it reads `Preparation unavailable`.

## Library

Library is the complete work list and the only place decisions are made. It
opens with its write handoff in place, so the notice never moves the works.

### Counts and lenses

Every global count comes from one summary and names its unit. The Attention
badge counts works needing a decision. Under a search the status reads `Found m
of n works` and the lens. A search narrows the list and Attention, never the
totals.

The lenses are All (the default), Needs attention, No attention needed and Not
matched, in one row that scrolls sideways when the panel is narrow. Lens,
search, cursors, selected work and field live in the URL, so deep links and
Back and Forward work. The pager offers only the moves that exist and is absent
when the works fit on one page.

Works needing a decision come first: those with complete provider evidence,
then those with a limited lane, under **Provider evidence complete** and
**Provider evidence limited**. Complete does not promise a match, and limited
does not promise a retry. The other lenses are alphabetical.

A work with no case, nothing to decide and no admitted provider evidence is
**Not matched**, counted beside Works as `7 works · 4 not matched`. Its status
gives one reason, in order: **Found, but not this work** (admission refused
what was found), **Providers unavailable** (adding **; will be checked again**
only while a recovery job is scheduled), or **No provider matched**. If the
recovery store cannot answer, Library drops only that promise and still loads
the page and its counts.

A work whose preparation could not finish stays a Preparation failed safely
row, not Not matched, even if an earlier report was unmatched. An ebook whose
candidates were found but not admitted as proposals stays in No attention
needed.

### Rows and selection

Rows show title, authors, library and format, never internal keys. Long titles
wrap within two lines. A work whose report retains its library's cover shows
it; others show a spine of initials. Search covers title, authors, library,
item and media, with wildcards treated literally. A work without a case offers
no detail action. A prepared audiobook with no case counts as recorded and
opens its evidence
([ADR-0069](../architecture/decisions/0069-audiobook-review-cases-and-terminal-outcomes.md)).

Selecting a work never moves the workspace. Wide screens select the first
reviewable work without recording anything; compact screens start with the
list. The selected work loads from its own case read, so a search or outcome
that moves it off the page leaves it on screen. It reads unavailable only when
that read fails.

## Attention

Attention is the queue of unresolved fields inside Library. Each work appears
once with its field count, and its fields follow. The current work, field and
position (`Work g of G`) stay beside the canvas. The rail follows the search,
reading `Matching: f of a works needing a decision`, and keeps its own cursor.
Load more moves focus to the first new row. An empty queue does not mean an
empty Library.

## Deciding a field

### The canvas

A rail lists the work's fields in order and stays after a decision. Each tab
names its status: Needs you, Decided, Automatic, Optional or No change. Empty
fields sit behind one `Show N more fields` tab.

The canvas shows current against proposed before evidence. Each choice carries
its evidence. `Recommended` appears only for an authoritative deterministic
selection. Recorded observations are listed separately and read-only. A
disclosure never chooses a value.

The server classifies every field, and the tab status follows from it:

- **Needs you:** queued. This includes a field where automatic handling
  abstained.
- **Decided:** an owner decision is recorded.
- **Automatic:** decided automatically. It can be inspected and deliberately
  overridden, but it is not queued.
- **Optional:** choosable but not counted; Next skips it.
- **No change:** context only. Keeping the current value is a choice only when
  an eligible alternative exists. Unsupported fields show their value and a
  reason, never a selectable `Not set`.

An Automatic field explains a withheld decision only when failed provider
evidence contributed to it, and the explanation goes when its cause does. An
unknown recorded reason reads as unknown. A hold from an earlier case says an
earlier owner decision is pausing automatic handling.

### Choosing

The current value and each eligible value are the commands, with no submit
step. A field's choices, and a cover comparison's cards, form one radio group:
one Tab stop, arrow keys move without recording, and Space, Enter or a tap
records once and moves to the next field. The recorded choice says "Your
choice".

The canvas advances only after the server acknowledges the decision and a case
read confirms it. Counts catch up afterwards. Recording is not applying. When every field is
recorded, the work lists each field and its outcome and offers Review next
work; this does not mean anything has been written.

If the command fails, the choice and its idempotency key are kept and one
explicit retry repeats it. Commands are never retried automatically. A lost
response gets one reconciliation attempt; an ambiguous result stays locked and
is never called unrecorded. If the work changed first, the page moves to the
current case and says nothing was recorded.

### Owner entry, withdrawal and choosing again

Where no alternative fits, **Enter a value** takes text for a title, subtitle
or publisher and suggests up to four cleaned forms of the current value. A
genre field offers **Choose genres** over current and proposed genres.
Recording or withdrawing keeps focus on the field
([ADR-0085](../architecture/decisions/0085-owner-entered-values-withdrawal-and-reselection.md)).

Revisiting a recorded field restores its choices with the recorded one marked;
a new choice appends a decision. A write in progress and a verified cover write
cannot be replaced; a verified reversible field write can.

There is no Undo and there are no bulk decisions. Going back means choosing
the earlier value: after a write, Enter a value suggests the value it replaced ("Use
previous value") and Choose genres tags those genres **Previous**. Authors return to an earlier value only when a
provider proposes it.

### Audiobooks

An audiobook uses the same rail, with Narrators and ASIN, and the same guarded
writes
([ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).
Until its recording identity qualifies
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)),
choices stay available but settled fills are not counted. Tag and ASIN fills
never queue alone; narrators queue only under "Authors and narrators: Ask me".

When only the book is supported, the view says the recording is unresolved
and offers series, title, description and genre, never subtitle. A description
or genre that only trims or extends the current value is optional, unless the
current value is a placeholder, contains markup, is visibly cut off, or lists
only formats.

A supported first book may share its title with its series. The canvas then
keeps the title and offers series name and position separately.

## Covers

Covers are compared side by side: the current cover and candidates equally in
a small set, or the current cover pinned beside a larger gallery. At 1024 pixels
and below they form one scrolling row. Each image sits whole in a 5:8 frame.
Excluded candidates stay subordinate with their reason.

Each candidate summarises its defect checks (clean, named defects, uncertain
or no reviewer evidence); other notes and sources sit in disclosures. None of
this confirms a match, changes order or earns automatic authority.

The owner may choose any intact retained alternative, even of lower quality or
with "Match not confirmed". One tap chooses; there is no dialog.
Contradictory identity stays excluded. A choice against an outdated comparison
shows the current one. A cover choice can be revised until its write begins.

## Evidence and History

`/review/{case}` is a read-only page with Evidence and History tabs. Neither
has decision controls or shows internal identifiers.

- **Evidence** lists observations by field with their source, and provider
  outcomes in a Sources disclosure. Missing lineage never claims a field is
  empty.
- **History** lists owner and automatic outcomes for the case, each with its
  own time, and shows a value only where the record links it exactly.

Both keep the Library lens, search, cursors and field, and Back to work returns
to the same work and field. Cross-work history is in Activity.

Audiobooks carry a Media health section: container mismatches, unexpected
codecs, missing running time, rights-managed audio, and running time short of
the matched edition. Observations refuse nothing and offer no action
([ADR-0074](../architecture/decisions/0074-headers-only-media-health-facts.md)).
An empty check reads `No media-health observations were recorded.` The same
section appears beside an audiobook case's fields in Library, beneath a row
whose latest preparation stopped but which keeps earlier evidence, and on a
failed preparation row. Those three show it only when it has an observation.

## Policy

Policy rules are in the [automation policy](automation-policy.md). Each option
leads with its behaviour: Automatic, Ask me, Ignore or unavailable. Limits
appear beside each option and in activation review.

A preview compares retained inputs; it never activates or writes. It separates
draft effects from projected library changes and shows the real abstention
cause. Sampled-work counts include unchanged works;
changed-field counts include only policy-caused changes. A rule no sampled
work evaluated reads
"Not previewed. This rule takes effect on the next preparation."
An edited draft is named "Custom policy".
Activation names the libraries that keep write permission, or says none does
and activating grants none. Rule codes appear only under "Technical detail".

## Activity

Activity is a permanent destination with no badge. Guarded state, recovery and
recent outcomes lead the page. Pause, resume and revoke are its only prominent
controls.

### Write permission

There is one Review write permissions entry, and Library's link opens the same
panel. Opening grants nothing. The review checks backup readiness, prepared
work, destination capabilities and one before-and-after example; nothing can be
confirmed before that check answers. The checked libraries survive refreshes
and retries. They are discarded, with the reason stated, when the basis
changes (libraries, destination capability or consent, or active policy), or
on cancel or navigation. Confirmation binds the exact visible set and fails
safely if it changes. A lapsed permission is stated in Library and Activity,
and neither reads as calm while no library holds permission
([ADR-0039](../architecture/decisions/0039-continuous-guarded-application.md)).

### The ledger

The ledger shows one row per change: the work or setting, the field, the exact
before and after, who chose it, and whether Audiobookshelf confirmed it.
Categories are owner choice, automatic choice, policy change and recovery.
Values come from the event's immutable record, so later changes never alter
history. A record that was not a change, such as a paused preparation, says
what happened instead. Automatic checks with nothing to write gather into one
row, "Checked N fields, no changes". Each Details control names its row's
field, work, result and time. Search, filters, a 7, 30 or 90-day range and 25, 50 or
100-row pages run on the server. An unknown filter shows every event and says
so.

## Settings

Settings has seven sections: Audiobookshelf, Libraries, Metadata sources,
Reviewer, Background work, Access and diagnostics, and Appearance. The section
is the `?group=` URL value. Above about 700 pixels a section index sits beside
the page; below, a labelled selector replaces it.

The page edits one draft of the whole configuration, with change counts per
section. Indirect changes count too: turning a source off removes it from its
libraries. A bottom tray, shared with Policy, appears while anything is unsaved,
lists before and after values, and offers Discard all and Save settings. A tray
taller than a third of the window scrolls with the page instead of floating. Save
validates everything and goes to the first invalid value. A save is confirmed
once, as saved and in use, in a notice above the page's end and the tray, so it
moves nothing. A stale save reloads the newer settings and keeps the draft on
top. An edit to a value that was
also changed elsewhere takes the newer saved value, and the tray says how many
did. In file mode the page names
`/data/config.json` and offers no edits
([configuration](../reference/configuration.md),
[ADR-0058](../architecture/decisions/0058-settings-authority-and-first-boot.md)).

Secrets show only as `Configured · ····xxxx` or `Not configured`. Disabling a
provider keeps its key, turns off its cover evidence and removes it from its
libraries. Turning it back on does not restore those libraries; add them
again.

Each library lists its languages by name, the first marked Preferred, with
buttons to move or remove each and a picker to add one, up to eight. Typing a
name in the open picker moves to it. In file mode the list is read only.

Reviewer sign-in, validation and tests take effect at once and are not part of
Save. Each reviewer task has a model picker with an example cost per 1,000
books. Replacing the shared OpenRouter key invalidates access for both tasks.
Usable advice grants no write
([ADR-0062](../architecture/decisions/0062-owner-managed-reviewer-access.md)).

## Words and values

Copy orients, explains a real consequence, resolves a status or gives a next
step; otherwise it is omitted. The owner never reads a code as words: an
unknown code is refused, not sentence-cased. Acronyms stay capitalised,
languages read by name with their tag ("English (en)"), and sizes use MB.
Times are relative within the past hour, the clock time today, and the date
otherwise; future times are never relative.

## Responsive and accessible behaviour

- From 761 to 1199 pixels Attention is a drawer: everything behind it except
  the toolbar with the Attention toggle is inert, and Tab stays inside. At 760
  pixels and below, the list, the work and Attention are separate steps, and
  choosing a work, from the list or Attention, scrolls Back to works just below
  the masthead.
- No page scrolls horizontally at 1440, 884, 390 or 320 pixels in either
  theme. At 200% text, Library, Policy, Activity and Settings keep every
  control reachable inside its container at every width, and Activity rows
  fold into cards. A word breaks inside itself only when wider than its line;
  data tokens may break anywhere.
- Focus never falls to the page. A running command keeps focus, marked
  `aria-disabled`, then moves to its outcome. A failed retry or an unreadable
  detail focuses Try again. Opening Attention focuses its heading; closing
  returns focus to the toggle. Tab closes a dropdown onto its trigger.
- Native controls, one heading outline per page, visible focus and polite
  status announcements throughout. Colour is never the only sign of status.
- Text meets 4.5:1, and boundaries and focus rings 3:1, in both themes. No
  text is smaller than `0.75rem`. Primary targets, lenses, disclosure
  summaries and action links are at least 44 pixels at every width.
- Reduced motion keeps every status and focus change without the movement.

## Performance and privacy

- No page embeds the whole library. Collections are paginated; detail,
  evidence and covers load on demand.
- Authenticated responses are private and never stored in shared caches.
- A strict content-security policy and text-only rendering stop metadata,
  provider content and URLs from becoming markup.
- Review endpoints never expose library paths, raw provider payloads or
  copyrighted content.
