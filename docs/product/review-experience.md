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
address while signed in goes straight to Library. Attention has no page of its
own: an old `/attention` address opens Library with Attention open.

The masthead holds still while the live-update and preparation status change.
It is sticky only while it takes at most a quarter of the window; taller, it
scrolls away, so it never covers content or focus. The current destination is
marked, and Library stays current while a review or Attention is open.

Navigation mounts without waiting for data, and loading reads as one small
line beside a dot that pulses only when motion is welcome. A pending or failed
count is never shown as zero, and a failed read keeps a retry. A page that
cannot load says why and what to do; the status and code are under Technical
detail. An older response never replaces a newer selection. While a newer read is pending, the last content
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
source lookups already waiting keep their schedule. The popover fits the window
below the masthead, scrolling inside itself when it must. `Escape` closes it and
returns focus to its trigger. Pending commands are disabled, not removed, so
focus stays put and a second press sends nothing. When a command's result
cannot be confirmed, the popover shows the refreshed state, says no answer came
back, and does not offer to try again.

Retry-shaped controls differ:

- `Scan now` and `Retry` start a new preparation cycle for one library.
- `Retry now` retries every waiting source lookup in every library.
- Retrying a cover or description consideration asks the reviewer about that
  one item again.

Limited lookups recover on their own schedule; a failure before a report has
no automatic retry ([provider recovery](../operations/provider-recovery.md)).

Library and Settings share one provider notice. Each entry names a source, a
library, the reason, how many lookups wait and when they retry. A refused key
is not retried automatically: fix it, then use Retry now (or the file
instruction). An exhausted lane says no retry remains. Each entry links to
Settings › Metadata sources. Retry now's outcome is announced beside
it; a file-managed deployment shows the file instruction instead. With nothing
waiting the notice takes no space.

A work whose preparation could not finish stays in Library as a
non-interactive row, Could not finish, with its title while the catalogue
still matches. Each failure code maps to one reason and one next step: a
change you make and then a scan, an automatic retry, or scanning the library
again, which queues a failed book again from the step that failed. That retry
rereads what earlier steps saved, so saved data that could not be read says
Library has no step that fixes it. Unknown codes are rejected, not given
invented guidance. The row shows its reason, next step and a copyable
reference, never a stage code, path or provider payload.
If its catalogue identity has changed it reads `Preparation unavailable`.

## Library

Library is the complete work list and the only place decisions are made. It
opens with its write handoff in place, so the notice never moves the works.

### Counts and lenses

Every global count comes from one summary and names its unit. The Attention
badge counts works needing a decision. Under a search the status reads `Found m
of n works` and the lens. A search narrows the list and Attention, never the
totals.

The lenses are All (the default), Needs attention, No attention needed, Not
matched and Could not finish, in one row that scrolls sideways when the panel is narrow. Lens,
search, cursors, selected work and field live in the URL, so deep links and
Back and Forward work. The pager offers only the moves that exist and is absent
when the works fit on one page. An empty list says so in one line; with no
library connected it links to Settings to add one.

Works needing a decision come first: those whose lookups all finished, then
those with a lookup that gave no usable answer, under **Every lookup
finished** and **Some lookups gave no usable answer**, with no caption. The
first does not promise a match, and the second does not promise a retry. Works
that could not finish follow under **Could not finish**, then Not matched, then
the rest. The other lenses are alphabetical.

A work with no case, nothing to decide and no admitted provider evidence is
**Not matched**, counted beside Works as `7 works · 4 not matched`. Its status
gives one reason, in order: **Found, but not this work** (a provider found
candidates, but work-identity admission refused them all, for audiobooks and
ebooks alike), **Sources unavailable** (reading **Sources unavailable, retry
scheduled** only while a recovery job is scheduled), **Failed in Mechanical
Librarian** (a source failed inside Mechanical Librarian itself, which is never
retried), or **No source matched**. If the
recovery store cannot answer, Library drops only that promise and still loads
the page and its counts.
An audiobook's iTunes listing is only its cover. It never matches the
audiobook, so one that only iTunes found reads **No source matched**. An iTunes
failure never makes its sources unavailable, and never puts it under **Some
lookups gave no usable answer**.

A work whose latest preparation could not finish reads **Could not finish**
and sits under that heading, counted beside Works as `2 could not
finish`, even if an earlier report was retained or unmatched. A decision still
open on a retained case keeps it in Needs attention. An ebook whose
provider matched it but offered no change worth a proposal stays in No
attention needed; one whose providers found only other works is Not matched,
Found, but not this work.

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

The canvas shows the question, one line under it with the field's own reason,
and current against proposed. The current value's card shows only its value; a
proposed card shows its value and, when known, the sources it came from, and
cards carry no `Alternative N` label. Each card's Evidence control sits
directly under its value. `Recommended` and its reason appear only for an
authoritative deterministic selection; nothing says when there is none.
Other source values appear only when the field has some or they could not be
read, and the writing-to-the-library box only when a change for the field is
recorded. A notice never restates the reason already shown, and a decision
carries no reminder that writes are protected. A disclosure never chooses a
value.

The server classifies every field, and the tab status follows from it:

- **Needs you:** queued. This includes a field where automatic handling
  abstained.
- **Decided:** an owner decision is recorded.
- **Automatic:** decided automatically. It can be inspected and deliberately
  overridden, but it is not queued.
- **Optional:** choosable but not counted; Next skips it. Its one reason says
  why no review is needed, and no comparison condition asking for a choice is
  shown beside it.
- **No change:** context only. Keeping the current value is a choice only when
  an eligible alternative exists. Unsupported fields show their value and a
  reason, never a selectable `Not set`. In Preview, a field that Automatic
  chose before you entered Preview mode reads No change, shows that choice
  beside the current value, and says turning on Automatic decides it again.

Fields with nothing to decide sit in one collapsed panel headed `No change:`
and their names, with any field waiting or unavailable counted separately;
each row gives its value and why nothing changed.

An Automatic field explains a withheld decision only when failed provider
evidence contributed to it, and the explanation goes when its cause does. An
unknown recorded reason reads as unknown. A hold from an earlier case says an
earlier decision keeps Automatic off for the field. Until requested fresh
checks finish, the field says they were requested, also after a reload. An
unconfirmed request reloads the case and says whether it shows the request.

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
provider proposes it. A work that returns to an earlier content reads as new:
the decisions recorded on it before are history, and the field shows only
what you decide now.

### Audiobooks

An audiobook uses the same rail, with Narrators and ASIN, and the same guarded
writes
([ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).
Until its recording identity qualifies
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)),
choices stay available but settled fills are not counted. Tag and ASIN fills
never queue alone; narrators queue only under "Authors and narrators: Ask me".

When only the book is supported, the view says the recording is unresolved
and offers series, title, description, genre, publication date and cover,
never subtitle. The date reads as the book's first publication. A narrator
choice from a Hardcover audio edition has its own sentence, is offered only for
an empty field, and each evidence row names its edition by number and runtime.
The cover gallery holds only the current cover and the covers the work route
offers. Such a cover reads "Same book · recording not confirmed" and says it
comes from the matched book, not a confirmed recording; its identity stays
unverified, so choosing it still confirms an unverified match, and History
says the same. The note's "Your choice applies only to this field" follows
the number of fields offered.
Recording identifiers and automatic changes stay unavailable. A description
or genre that only trims or extends the current value is optional, unless the
current value is a placeholder, contains markup, is visibly cut off, or lists
only formats.

A supported first book may share its title with its series. The canvas then
keeps the title and offers series name and position separately.

When Mechanical Librarian found the matched recording by a title other than
the audiobook's own, a note says how, at the top of the Evidence page, in
Recorded evidence and in Library's Recording match section. The section is named
"How this recording was matched", quotes what was searched, and offers the
Recording source as the chapter note does (#1723):

- "Mechanical Librarian found the matched recording by searching with “…”,
  read from this folder's name, which differs from this audiobook's title."
- "Mechanical Librarian found the matched recording by searching with “…”,
  read from the title tag in this audiobook's audio file, which differs from
  this audiobook's title."
- "Mechanical Librarian found the matched recording by searching with “…”, the
  title of the EPUB in this folder, which differs from this audiobook's title."
- "Mechanical Librarian found the matched recording by searching with the title
  and author(s) of the EPUB in this folder, “…” by …, because this audiobook's
  own title or author is missing or incomplete."
- "Hardcover lists the matched recording as an audio edition of the book with
  ISBN …, which is stated in the EPUB in this folder."

Each is followed by "Its running time closely matches this audiobook's." (for
the ISBN, "The matched recording's running time …"). No note appears when the
audiobook's own title and authors also found the recording.

## Covers

Covers are compared side by side: the current cover and candidates equally in
a small set, or the current cover pinned beside a larger gallery. At 1024 pixels
and below they form one scrolling row. Each image sits whole in a 5:8 frame.
Excluded candidates stay subordinate with their reason.

Each candidate summarises its defect checks (clean, named defects, uncertain,
or no result when the visual review ran); other notes and sources sit in
disclosures. None of
this confirms a match, changes order or earns automatic authority.

The owner may choose any intact retained alternative, even of lower quality or
with "Match not confirmed". One tap chooses; there is no dialog.
Contradictory identity stays excluded. A choice against an outdated comparison
shows the current one. A cover choice can be revised until its write begins.

### Covers view

Library switches between **Works** and **Covers** with one control above each
view's heading. Covers lists every work with a review case by what its cover
asks of the owner, under four tabs with their counts: **To decide** (a
suggested switch, then an open choice, under **Switch suggested** and
**Choose**), **Kept** (the current cover stays and others can still be
chosen), **Switched** and **All** (a wall of every cover, with each title and
author). To decide counts exactly what Library's attention counts for covers,
and the counts follow each decision. Pages hold 50 books, with **Show more
covers** for the next; a page resumes after the last book shown, so a
decision never makes it skip one. The tab and the open book live in the URL,
and pages are private and never cached. A wall tile reads **No cover** when
the cover field is empty and **Image unavailable** when the library's cover
could not be read; a queue thumbnail, too small for the words, shows an empty
frame and leaves the state to screen readers and the open book.

On a wide screen To decide and Kept open their first undecided book beside
the list; on a phone the list comes first and **Back to books** returns to
it. A book shows its comparison, asking "Which cover should this book use?".
One tap records the choice through the work's own decision command and opens
the next book; **Skip for now** moves on and decides nothing. In All the book
opens below its row, with **Close**. A book whose covers changed since the
page loaded is refused on its own with **Reload**, which reopens the same
book with its current covers; the others stay usable. If no answer comes
back, the book says so rather than guessing.

**Switched** lists automatic cover writes the owner has not answered since,
newest first, as **Before** and **Now** with each cover's size; a write that
filled an empty cover reads **No cover** before. The original is listed only
while its copy is retained
([ADR-0093](../architecture/decisions/0093-measured-cover-rules-and-current-cover-protection.md)).
**Keep** records keeping the current cover, releases that copy and marks the
row **Kept**; **Others** opens the comparison. Both appear only when another
cover can be chosen, since keeping the current cover is a choice only beside
another. A cover field in the work view links to its book in Covers, which
opens it wherever it falls in All.

## Evidence and History

`/review/{case}` is a read-only page with Evidence and History tabs. Neither
has decision controls or shows internal identifiers.

- Evidence lists what each source said for each field, with each lookup's
  result under Sources. When source answers are no longer available, it says
  how many and never calls a field empty. The ISBN lookup for an audiobook's
  EPUB reads "ISBN lookup (EPUB in this folder)" here, in the work summary
  and in the comparison footer (#1723). An audiobook's iTunes lookup
  is for a cover only, so a listing it finds reads "Cover found" (#1779).
- History lists your choices and automatic outcomes for the case, each with
  its time. It shows a value only where the record links it exactly.

Both keep your place in Library, and Back to work returns to the same work and
field. History across works is in Activity.

An audiobook can carry a Media health section about its audio, such as an
unexpected container or format, encrypted audio, or a running time missing or
shorter than the edition. The section blocks no choice and offers no action
([ADR-0074](../architecture/decisions/0074-headers-only-media-health-facts.md)).
It appears only with something to report: in a work's Recorded evidence, a
review case's Evidence, and beside a Library row whose latest preparation
stopped, including Could not finish rows.

## Policy

Policy rules are in the [automation policy](automation-policy.md). Each field
leads with its setting: Automatic, Ask me, Ignore or Not supported. Limits
appear beside each option and in activation review.

A preview runs the draft on recorded evidence and changes nothing. It lists
the changes the draft causes apart from Unchanged outcomes, and says why when
the draft would choose nothing. Sampled works include those with no change,
and changed fields count only what the draft causes. A rule no sampled work
reached reads "Not previewed. This rule takes effect on the next preparation."
An edited draft is named "Custom policy". Activation names the libraries that
keep write permission, or says none has it and activating grants none. Rule
codes appear only under "Technical detail".

## Activity

Activity is always in the masthead and has no badge. While a change waits for
you, such as a description to restore in Audiobookshelf, the page never says
no action is needed. Pausing new work and removing a library's write
permission are in a section you open.

### Write permission

Review write permissions opens one panel, from Activity or from Library's
write handoff, and opening it grants nothing. Mechanical Librarian then checks
that its own database is consistent and shows the prepared work, the fields
each library can write and one before-and-after example. You can confirm only
once that check passes and you include at least one library.

The libraries you include stay included through page updates and repeat
checks. A change to the libraries, their fields' write support or permission,
or the active policy clears your choice and says why. Cancel or leaving the
page clears it too. Confirming grants only the libraries you included. If the
libraries, their fields, the policy or the database check changed since, it
grants nothing and says so. Library and Activity both state a lapsed
permission, and neither says no action is needed while no library has permission
([ADR-0039](../architecture/decisions/0039-continuous-guarded-application.md)).

### The ledger

The ledger shows one row per change: the work or setting, the field, the exact
before and after, who chose it, and whether Audiobookshelf confirmed it.
Categories are your choice, automatic choice, policy change and recovery.
Values come from the record made at the time, so later changes never alter
history. A record that was not a change, such as a paused preparation, says
what happened instead. Automatic checks with nothing to write gather into one
row, "Checked N fields, no changes". Each Details control names its row's
field, work, result and time. Search, filters, a 7, 30 or 90-day range and 25,
50 or 100-row pages run on the server. An unknown filter shows every event and
says so.

## Settings

Settings has seven sections: Audiobookshelf, Libraries, Metadata sources,
Reviewer, Background work, Access and diagnostics, and Appearance. The section
is the `?group=` URL value. Wider than 760 pixels, a section index sits beside
the page, and at 760 or narrower a selector replaces it.

The page edits one draft of all settings, and each section counts its changes,
indirect ones too. While anything is unsaved, a bottom tray shared with Policy
lists before and after values and offers Discard all and Save settings. A tray
taller than a third of the window scrolls with the page. Save settings checks
every value and goes to the first one that needs fixing. "Settings saved and
in use." appears once, above the page's end and the tray, so nothing moves. If
settings changed elsewhere, saving loads them under your draft. An edit to a
value changed in both places takes the newer value, and the tray counts these.
In a file-managed deployment the page names `/data/config.json` and offers no
edits ([configuration](../reference/configuration.md),
[ADR-0058](../architecture/decisions/0058-settings-authority-and-first-boot.md)).

Secrets show only as `Configured · ····xxxx` or `Not configured`. Turning a
source off keeps its key, turns off its cover evidence and removes it from its
libraries. Turning it back on does not add it to those libraries again.

Each library lists its languages by name, the first marked Preferred, with
buttons to move or remove each and a picker to add one, up to eight. In a
file-managed deployment the list is read only.

Reviewer sign-in, validation and tests take effect at once, outside Save
settings. Each reviewer task has a model picker with an example cost per 1,000
books. Both tasks share one OpenRouter key, and replacing it keeps both model
choices. Reviewer advice allows no write
([ADR-0062](../architecture/decisions/0062-owner-managed-reviewer-access.md)).

## Words and values

The voice is the one `DESIGN.md` states under Voice. Mechanical Librarian
never turns a code into words: it refuses an unknown code instead of
sentence-casing it. Acronyms stay capitalised, languages read by name with
their tag ("English (en)"), and sizes use MB. Times within the past hour are
relative, others on the same day show the clock time, and other days add the
date. A time still ahead is never relative.

## Responsive and accessible behaviour

- From 761 to 1199 pixels Attention is a drawer. Everything behind it except
  the toolbar with the Attention toggle is inert, and Tab stays inside. At 760
  pixels and below, the list, the work and Attention are separate steps.
  Choosing a work scrolls Back to works to just below the masthead.
- No page scrolls sideways at 1440, 884, 390 or 320 pixels in either theme. At
  200% text, Library, Policy, Activity and Settings keep every control
  reachable inside its container at every width, and Activity rows fold into
  cards. A word breaks inside itself only when wider than its line, and data
  tokens can break anywhere.
- Focus never falls to the page. A running command keeps focus, marked
  `aria-disabled`, then moves to its outcome. A failure message sits under its
  control. A failed retry or an unreadable detail focuses Try again, or Reload
  when the tab is on an earlier build. A retry that succeeds focuses what it
  loaded. Opening Attention focuses its heading, and closing it returns focus
  to the toggle. Tab closes a dropdown onto its trigger.
- Every page uses native controls, one heading outline, visible focus and
  polite status announcements. Colour is never the only sign of status.
- Text meets 4.5:1, and boundaries and focus rings 3:1, in both themes. No
  text is smaller than `0.75rem`. Primary targets, lenses, disclosure
  summaries and action links are at least 44 pixels at every width.
- Reduced motion keeps every status and focus change without the movement.

## Performance and privacy

- No page embeds the whole library. Collections come in pages, and detail,
  evidence and covers load on demand.
- Signed-in responses are private, and shared caches never store them.
- A strict content-security policy and text-only rendering stop metadata,
  source content and URLs from becoming markup.
- Review endpoints never expose library paths, raw source responses or
  copyrighted content.
