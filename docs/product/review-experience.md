# Review experience

This is the product contract for the workspace you sign in to: first run,
Library, Attention, Evidence and History, Policy, Activity and Settings.
[PRODUCT.md](../../PRODUCT.md) sets the boundaries, and the
[automation policy](automation-policy.md) decides which work can be automatic.

## Principles

- Items stay available in Audiobookshelf while curation runs in the background.
- Mechanical Librarian settles qualified, non-destructive work without you.
  Attention holds only work where your decision can help.
- Every decision covers one field of one library item and stands alone.
- Review makes no destructive changes ([PRODUCT.md](../../PRODUCT.md)).
- The server holds every decision. A refresh, another device, a restart or an
  upgrade cannot lose an acknowledged one, unless the upgrade needs a clean
  rebuild. Browser storage holds display preferences only.

## Review cases

A review case covers one library item as it stood at one moment: its library
connection, its internal identity, its fingerprint and its proposals
([review cases](../operations/review-case.md)). A new fingerprint or any change
to its proposals replaces an unresolved case. Mechanical Librarian never
changes a recorded case or decision. The same item in two libraries is two
separate items, and titles and authors never decide identity.

A case holds the current metadata and cover, and says when either is missing
or not loaded. It holds the field proposals, the cover and series choices and
references to their evidence. It also holds the rule-based recommendation,
what your policy does with each field, and your current decision. Identical
proposed values become one choice, which keeps every source and every
disagreement. Evidence alone never adds a row to Attention. A field that needs
you never holds back a field that is ready on the same item. Rejected
alternatives and those that change nothing stay on record, and Mechanical
Librarian never writes them.

An ebook and its audiobook are separate works with separate cases. Deciding
one never changes the other. Mechanical Librarian never treats two works as
the same work because their titles are similar.

Mechanical Librarian keeps these apart: the evidence, the recommendation, what
your policy does with the field, your decision, write permission, and the
write with its check. A recommendation alone never decides a field or allows a
write. An Automatic decision needs its own qualification, tied to the exact
case and policy version
([ADR-0036](../architecture/decisions/0036-qualified-decision-only-automatic-policy.md)).
Every write, yours or automatic, also needs the library's write permission and
unchanged evidence and library values. Before the write, Mechanical Librarian
checks that its own database is consistent, and afterwards it reads the value
back ([ADR-0037](../architecture/decisions/0037-origin-neutral-guarded-field-effects.md)).

For each field you can keep the current value or choose an eligible proposed
value. You can enter a value where none fits, or withdraw a decision that has
not been written. You can choose again a value that a write replaced, or leave
the field undecided. Blocked is a state, not a decision. Source outages and
retries show as preparation status, never as a metadata choice.

## First run

An empty data volume opens a six-step setup: your password, the
Audiobookshelf connection, libraries, metadata sources, an optional cover
reviewer and Start. A seventh step, Find library folders, appears only when
setup cannot find a library's folder. [Deployment](../operations/deployment.md)
covers the claim token and container.

- Each step opens on its title and controls, with no checklist before it. A
  refusal sits beside its field and clears when the field changes. It gives a
  safe cause and one remedy, never a code, and stays in view at 200% text.
- Completed steps collapse to a row with Change. The step list stays beside
  the step while it fits the window, and scrolls inside itself when it does
  not. Reopening a step removes only the answers and secrets that depend on
  it. Resuming setup returns to the first incomplete step. A change in another
  tab restores the latest recorded step.
- The password step shows a one-line plain-HTTP notice only when the page is
  not served over HTTPS.
- A failed connection names its cause, the address and one remedy. The causes
  include a name that does not resolve, a refused connection, an unverified
  certificate, no answer in time and a rejected key. Setup refuses an address
  with a path, user name, password, query or fragment before it sends any
  request, and warns before sending a key over plain `http://` to an address
  outside your private network.
- Every book library starts ticked under its Audiobookshelf name, and setup
  says once that podcast libraries are not shown. Two libraries cannot share a
  name in any letter case; rename one in Audiobookshelf or untick one.
- On Continue, setup checks each library's folder from the path
  Audiobookshelf reports. When every folder is found, the next step says so
  in one line. Otherwise Find library folders lists only the libraries it
  could not find, each Found or Not found with what to do, and checks again.
  Setup never creates a mount.
- Setup groups metadata sources as Books and ebooks, and Audiobooks. The
  sources that suit the chosen libraries start ticked, and each can be
  unticked before Continue. Hardcover and Google Books need an API key while
  they are on. Without either, setup says most ebooks will wait for your
  review, and still lets you continue.
- The reviewer step checks an existing Codex sign-in by itself and offers one
  Sign in with ChatGPT button. OpenRouter starts on the recommended model with
  its cost per 1,000 works; other models wait behind Choose another model, and
  prices and data use sit in a closed section. Continue and Skip for now sit
  together at the bottom. You set up description advice later, in Settings.
- Secrets live only in their inputs, each with an accessible reveal control.
  Setup clears the reviewer key after each attempt. An expired session keeps
  every recorded step.

Before Start a summary lists each answer in step order, and each row reopens
its step. It says once: `Nothing in your library changes. Writes stay off until
you turn them on in Activity.` Start then switches the same address to the
workspace, starts preparation and opens Library, signed in, on the new run
when it is known within a few seconds. Library's write handoff appears once a
prepared change waits for write permission.

## Workspace shell

The destinations are Library (with Attention and work detail), Policy, Activity
and Settings. Every page opens in the saved theme, or the system theme until
one is saved, on its first frame.

Sign-in has Show password. It says whether the password was wrong or the device
must wait five minutes after too many attempts. The app has no password reset
([Forgotten owner password](../operations/deployment.md#forgotten-owner-password)).
Signing in returns to the page you opened, or to Library, and opening the
address while signed in goes straight to Library. Attention has no page of its
own: an old `/attention` address opens Library with Attention open.

The masthead holds still while the live-update and preparation status change.
It stays at the top only while it takes at most a quarter of the window. When
it is taller, it scrolls away, so it never covers content or focus. The
masthead marks the current destination, and Library stays marked while a
review or Attention is open.

Navigation appears without waiting for data. Loading reads as one small line
beside a dot. The dot pulses unless reduced motion is set in your system or in
Settings › Appearance. A count that is loading or failed never shows as zero,
and a failed read keeps a retry. A page that cannot load says why and what to
do. Its status and code are under Technical detail.

An older response never replaces a newer selection. While a newer read is
pending, the last content stays, marked busy with its controls inert. The page
announces the new route only when the read settles. Your actions take priority
over background refreshes. While preparation publishes works, an active tab
refreshes Library and Attention at most once every 1.5 seconds. After 10
seconds without input, a tab refreshes only the preparation panel. It reads
Library again on your next input or when the tab returns to the foreground. An
unknown review path shows a recovery state, not an error.

## Preparation and source status

The preparation panel states one run in five counts. Their labels are "ready
to browse", "recorded, nothing to decide", "not matched", "could not finish"
and "in progress". Ready to browse and in progress always show. The others hide
at zero, and an unknown count reads `…`. Counts describe the run's books as
they stand now. Each stage bar counts books that first reached it in this run.
A book that could not finish stops at its last stage. Bars glide to each new
count unless reduced motion is set. The status line's wipe plays only when the
run's state changes. Recent completions record each book's first finish, one
row each: the title, linked into Library, then "Prepared" or "Couldn't
finish". A rate in works a minute appears only while the run is running.

The masthead's background-work control shows each library's scan state and
counts, and marks a library outside the current run. At idle it reads
`Preparation idle` and still offers Scan now and Pause. Pause stops new scans.
Source lookups already waiting keep their schedule. The popover fits the window
below the masthead, and scrolls inside itself when it must. `Escape` closes it
and returns focus to its trigger. The popover disables a pending command
instead of removing it, so focus stays put and a second press sends nothing.
When no answer confirms a command's result, the popover shows the refreshed
state, says no answer came back and does not offer to try again.

These controls retry different things:

- `Scan now` and `Retry` start a new preparation cycle for one library.
- `Retry now` retries every waiting source lookup in every library.
- Retrying a cover or description consideration asks the reviewer about that
  one item again.

Lookups that a source is limiting retry on their own schedule. A failure before
a report has no automatic retry
([provider recovery](../operations/provider-recovery.md)).

Library and Settings share one notice of source lookups waiting to retry. Each
entry names a source, a library, the reason, how many lookups wait and when
they retry. Mechanical Librarian does not retry a refused key on its own: fix
the key, then use Retry now (or the file instruction). An entry with no
automatic retry left says so. An entry that needs an API key added or checked
links to Settings › Metadata sources. Mechanical Librarian announces the
outcome of Retry now beside it. A file-managed deployment shows the file
instruction instead. With nothing waiting, the notice takes no space.

A work whose preparation could not finish stays in Library as a row that does
not open, marked Could not finish. It keeps its title while the catalogue
still matches. Each failure code has one reason and one next step. The next
step is a change you make followed by a scan, an automatic retry, or a new
scan of the library. A new scan queues a failed book again from the step that
failed. That retry rereads what earlier steps saved. So when saved data could
not be read, the row says "Library has no step that fixes this." Mechanical
Librarian refuses an unknown code and invents no guidance for it. The row
shows its reason, next step and a copyable reference, never a stage code, path
or raw source response. Once the catalogue no longer matches, the row reads
`Preparation unavailable`.

## Library

Library is the complete work list and the only place you make decisions. It
opens with its write handoff in place, so the notice never moves the works.

### Counts and lenses

Every library-wide count comes from one summary and names its unit. The
Attention button counts works that need a decision. Under a search the status
reads `Found m
of n works` and the lens. A search narrows the list and Attention, never the
totals.

The lenses are All (the default), Needs attention, No attention needed, Not
matched and Could not finish, in one row that scrolls sideways when the panel
is narrow. Lens, search, page, selected work and field live in the URL, so
links and Back and Forward work. The pager offers only the moves that exist.
An empty list says so in one line, and with no library connected it links to
Settings to add one.

Works that need a decision come first, under "Every lookup finished" and then
"Some lookups gave no usable answer". The first heading does not promise a
match, and the second does not promise a retry. Works that could not finish
follow under Could not finish, then Not matched, then the rest. The other
lenses are alphabetical.

A work with no case, nothing to decide and no source evidence accepted for it
is Not matched, counted beside Works as `7 works · 4 not matched`. Its status
gives the first reason that applies. "Found, but not this work" means sources
found books, but none of them matched this work. "Sources unavailable" reads
"Sources unavailable, retry scheduled" only while a retry is scheduled.
"Failed in Mechanical Librarian" is never retried. Otherwise the status reads
"No source matched".

An audiobook's iTunes listing is only its cover. It never matches the
audiobook, so an audiobook that only iTunes found reads "No source matched".
An iTunes failure never makes its sources unavailable or puts it under "Some
lookups gave no usable answer".

A work whose latest preparation could not finish reads Could not finish and
sits under that heading, counted beside Works as `2 could not
finish`. This holds even when an earlier report exists, matched or not. A
decision still open on its earlier case keeps it in Needs attention. An ebook
that a source matched without offering a change worth proposing stays in No
attention needed.

### Rows and selection

Rows show title, authors, library and format, never internal keys. Long titles
wrap within two lines. A work whose report recorded its library's cover shows
it, and others show a spine of initials. Search covers title, authors,
library, Audiobookshelf item ID and format, and takes wildcards literally. A
work without a case opens a summary with no decision controls. Could not
finish rows with no report do not open. A prepared audiobook with no case
counts as recorded, and its summary shows its evidence
([ADR-0069](../architecture/decisions/0069-audiobook-review-cases-and-terminal-outcomes.md)).

Selecting a work never moves the workspace. Above 760 pixels, Library opens
the first reviewable work and records nothing. At 760 or narrower the list
comes first. Library reads the selected work on its own, so a search or
outcome that moves it off the page leaves it open. It says it could not be
loaded only when that read fails.

## Attention

Attention is the queue of undecided fields inside Library. Each work appears
once with its field count, and its fields follow. The current work, field and
position (`Work g of G`) stay beside the canvas. The queue follows the search,
reading `Matching: f of a works needing a decision`, and keeps its own place.
Load more attention moves focus to the first new row.

## Deciding a field

A row of tabs lists the work's fields in order and stays after a decision.
Each tab names the field's status:

- Needs you means the field is in Attention, including a field where Automatic
  made no choice.
- Decided means your decision is recorded, or an earlier decision of yours
  still holds the field.
- Automatic means Automatic decided the field. You can still change it, and it
  is not in Attention.
- Optional means you can choose, but the field is not counted and the canvas
  skips it when it moves on. Its one reason says why no review is needed, and
  it shows no condition asking you to choose.
- No change means the field is there for reference. Keeping the current value
  is a choice only when another eligible value exists. Each such field shows
  its value and a reason, never a selectable `Not set`. In Preview, a field
  that Automatic chose before you entered Preview mode reads No change. It
  shows that choice beside the current value and says turning on Automatic
  decides it again.

Fields that are empty and have no proposal sit behind one `Show N more fields`
tab. Fields with nothing to decide sit in one closed panel headed `No change:`
and their names. The heading counts any field waiting or unavailable
separately, and each row gives its value and why nothing changed.

The canvas shows the question, one line under it with the field's own reason,
and current against proposed. The current value's card shows only its value. A
proposed card shows its value and, when known, the sources it came from, but
no `Alternative N` label. Each card's Evidence control sits directly under its
value. `Recommended` and its reason appear only on the value your settings
choose, and the canvas says nothing when they choose none. Other source values
appear only when the field has some or they could not be read. The Writing to
the library box appears only when a change for the field is recorded. A notice
never restates the reason already shown, and a decision carries no reminder
that writes are protected. A disclosure never chooses a value.

When a field set to Automatic gets no automatic choice, the canvas says why
until the cause goes. An unknown reason reads as unknown. A hold from an
earlier case says an earlier decision keeps Automatic off for the field. Until
requested fresh checks finish, the field says they were requested, also after
a reload. If no answer confirms the request, Library reloads the case and says
whether it shows the request.

### Choosing

A field's choices, and a cover comparison's cards, form one radio group with
one Tab stop and no submit step. Arrow keys move without recording. Space,
Enter or a tap records the choice once and moves to the next field. The choice
you recorded says "Your choice".

The canvas moves on only after the server accepts the decision and a fresh
read of the case confirms it. Counts catch up afterwards. Recording a choice
does not write it. When every field is recorded, the work lists each field
and its outcome and offers Review next work.

If a choice could not be confirmed, the page keeps it, and Try the same
decision again sends it once more, never recording it twice. Mechanical
Librarian never resends it on its own. If no answer comes back, the page
reloads the case once and never says the choice was not recorded. If the work
changed first, the page moves to the current case and says nothing was
recorded.

### Entering a value and choosing again

Where no proposed value fits, Enter a value takes text for a title, subtitle
or publisher and suggests up to four cleaned forms of the current value. A
genre field offers Choose genres over the current and proposed genres.
Recording, or choosing Withdraw on an unwritten decision, keeps focus on the
field
([ADR-0085](../architecture/decisions/0085-owner-entered-values-withdrawal-and-reselection.md)).

When you return to a recorded field, its choices come back with the recorded
one marked, and a new choice adds a decision. You cannot replace a decision
while its write is in progress, or once a cover write is verified. You can
replace a verified field write that can be reversed.

Library has no Undo and no bulk decisions. To go back, choose the earlier
value. After a write, Enter a value suggests the value it replaced, as "Use
previous value", and Choose genres marks those genres Previous. Authors return
to an earlier value only when a source proposes it. A work whose content
returns to an earlier state reads as new. Its earlier decisions are history,
and the field shows only what you decide now.

### Audiobooks

An audiobook uses the same tabs, with Narrators and ASIN added, and its writes
need the same checks
([ADR-0071](../architecture/decisions/0071-book-media-guarded-application.md)).
Until its recording qualifies
([ADR-0073](../architecture/decisions/0073-audiobook-decision-qualification.md)),
an undecided field outside Attention still offers its choices and reads
Optional. Tag and ASIN fills never enter Attention on their own. A narrator fill
enters it only when Authors and narrators is set to Ask me.

When the evidence supports only the book, Recording match says the exact
recording is not known. It also says your choices apply only to the fields
offered, and that this evidence cannot set recording identifiers or make
automatic changes. The canvas then offers choices only for series, title,
description, genre, publication date, cover and an empty Narrators field, never
subtitle. When the book is the first in its series and shares the series' title,
the canvas keeps the title and offers the series name and position separately.
The date offered is the book's first publication. A narrator choice comes from a
Hardcover audio edition and has its own sentence in Recording match. Each
evidence row names that edition by number and running time. A description or
genre that only trims or extends the current value reads Optional, unless the
current value is a placeholder, has markup, is cut off or lists only formats.

The cover comparison then holds only the current cover and covers from the
matched book. Each one you can choose reads "Same book · recording not
confirmed" and says it is from the matched book. Choosing one still confirms an
unverified match, and History says so.

When Mechanical Librarian found the matched recording by a title other than the
audiobook's own, a note says how. It appears at the top of the Evidence page, in
Recorded evidence and in Library's Recording match section. Named "How this
recording was matched", it quotes what was searched and, like the chapter note,
has a Recording source (#1723):

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

The cover comparison shows three covers or fewer side by side, equally. With
more, the current cover is pinned beside a gallery headed Other covers. At 1024
pixels or narrower they form one scrolling row. Each image sits whole in a 5:8
frame.

When the visual review has run, each cover sums up its defect checks: none
found, the defects found, uncertain checks or no result. Other notes sit under
Visual check details. Nothing the visual review finds confirms a match, changes
the order or lets Automatic choose a cover.

You can choose any other cover whose image loads, even a lower-quality one or
one marked "Match not confirmed" or, with Book matching set wider, "Same title
and author", with no extra step. A cover that contradicts the book stays in the
row, but you cannot choose it. It reads "Contradicts this book" and says whether
a source says it belongs to a different book or edition, or names a different
author or contributor. With Book matching set wider, an author whose initials
are written differently does not count as a contradiction. A choice against an
outdated comparison shows the current one.

### Covers view

A control above each heading switches Library between Works and Covers. Covers
groups every work with a case by what its cover asks of you, under four tabs
with their counts. To decide lists suggested switches under Switch suggested,
then open choices under Choose. Kept lists books that keep their current cover
while you can still choose another. All is a wall of every cover, with each
title and author.

A suggested switch is a cover the measured cover rules would replace
([ADR-0093](../architecture/decisions/0093-measured-cover-rules-and-current-cover-protection.md)),
held for you because Current covers or the missing qualification does not let
it switch by itself. Its row carries a pill naming the fault, such as "Under
300 px" or "Square on an ebook", and its book reads "Suggested because" and
the reason. To decide counts the works whose cover field is in Attention plus
the suggested switches; Library's Needs attention counts only the first,
because a suggestion is optional. A readable cover with no fault is kept
without asking and appears under Kept. Pages hold 50 books, and Show more covers loads the
next. It starts after the last book shown, so a decision never makes you miss
one. The URL keeps the tab and the open book. A wall tile reads "No cover" when
the cover field is empty and "Image unavailable" when the library's cover cannot
be read. A list thumbnail shows either state as an empty frame. Screen readers
and the open book give the state.

Above 1024 pixels, To decide and Kept open their first undecided book beside the
list. At 1024 or narrower the list comes first, and Back to books returns to it.
One tap records your choice, as the work's own page does, and opens the next
book. Skip for now moves on without deciding. In All, a book opens below its
row with Close, and a choice closes it. If a book's covers changed since the
page loaded, Covers refuses that choice and offers Reload, which reopens the
book with its current covers. The other books stay usable. If no answer comes
back, the book says so and asks you to check before choosing again.

Switched lists automatic cover writes you have not answered since, newest first.
Each row shows Before and Now with each cover's size, and a write that filled an
empty cover reads "No cover" under Before. A replacement names its fault in a
pill and says why in one line, such as "The cover was 200 × 300 px, under 300
px on its short side." A Show menu above the list narrows it to one fault; a
fill has no fault, so it appears only under All. A replacement stays listed only while
Mechanical Librarian keeps a copy of the old cover
([ADR-0093](../architecture/decisions/0093-measured-cover-rules-and-current-cover-protection.md)).
Keep records that you keep the current cover, releases that copy and marks the
row Kept. Others opens the comparison. Both appear only when you can choose
another cover.

Once a replaced cover's work is prepared again after its switch, you can choose
the old cover. Its row then also offers Put back, and the comparison names the
old cover Replaced cover. An unreadable one shows "Image unavailable" there and
under Before (#1362). Put back records choosing the
old cover, and its write uploads it from the kept copy. The row reads Put back
while that write runs, and leaves Switched when the write is verified, which
releases the copy. If the cover was changed in Audiobookshelf first, the row
says "The cover was changed in Audiobookshelf, so it was not put back." and
offers Keep and Others again.

A cover field's Covers link opens its book in All, wherever it falls. The
fifth tab is [Taste profile](cover-taste-profile.md).

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
no action is needed. Pausing writes and removing a library's write
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
is the `?group=` URL value. A message elsewhere that names Settings → Reviewer
or Settings → Audiobookshelf links to that section. Wider than 760 pixels, a
section index sits beside the page, and at 760 or narrower a selector replaces
it.

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
settings. Each reviewer task has a model picker, with a cost per 1,000 works for
covers and descriptions. One OpenRouter key serves all three; replacing it
keeps every choice. Reviewer advice allows no write
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
