# Library progress

Library progress says where the whole library stands now: how many works are
settled, how many need you, and which fields they need you for. It is counted
on request each time it is read and never stored.

## The line

One line carries the counts, for example "1,840 of 2,026 works settled · 174
need you". Settled is Library's No attention needed lens and need you is its
Needs attention lens. " · 3 could not finish" and " · 9 not matched" follow
only when they are not zero, so the parts add up to All. A single work reads
"1 of 1 work settled" and "1 needs you". Numbers group their digits ("1,840"),
as every count of works does, including the Works count beside Library's
heading.

Where the line links, each phrase whose count is not zero is the link to its
Library lens: "1,840 of 2,026 works settled" opens No attention needed, "174
need you" Needs attention, "3 could not finish" Could not finish and "9 not
matched" Not matched. A phrase whose count is zero is plain text, because its
lens would be empty. A count not yet known reads `…`, never 0.

## In Library

When a preparation run finishes, cleanly or with failures, its box at the top
of Works gains the line under the run's button, within one frame and below a
rule, then Open in Activity on its own line, which opens Activity at the
Library progress card. The line and its links sit beside the button, never
inside it, so the button keeps its name and still opens the run. The line
counts the whole library, not the run.

The line is hidden while a run is starting, running or paused, and while the
service needs a restart. It returns when a run finishes, and it goes when you
dismiss the run in its detail; it has no dismissal of its own. If the counts
cannot be read, the line says "Library counts could not be loaded." with Try
again. On a tab from an earlier build it is left out, since the page already
offers Reload.

## On Activity

While Library has works, Activity has a Library progress card after the status
box and above the ledger. It shows the linked line for the whole library. With
two or more libraries, "By library" repeats the line for each one under its
name. "Fields that need you" lists each field with works that need you, most
first, such as "Cover 120 works". These rows are plain text, because Library
has no filter by library or field.

At 760 pixels and narrower the ledger comes first, so the card follows the
ledger and the status box, shown in full.

While the counts load, the card says "Counting works…" and the line reads `…`.
A failed read says "Library progress could not be loaded." with Try again, and
a tab from an earlier build offers Reload. A link to
`/activity#activity-library-progress` moves focus to the card once it has
loaded. The card follows Library's live updates, so its counts move as works
are decided.

## New since your last visit

While works have arrived since your last visit, Library has a New lens,
straight after All, with their count: "New 2". With nothing new the lens is
not there. Chosen, it lists those works in All's order under "2 new works
since your last visit", or "1 new work since your last visit". Under a search
the status reads "Found 1 of 2 works"; a screen reader also hears "new since
your last visit". An address that names the lens when nothing is new opens it
and says "No works here."

Nothing is dismissed. A visit ends when Library has not been used for half an
hour on any device. Works you opened New on stop being new at your next visit;
works that arrived without that stay new. The mark is kept on the server, so
every device agrees, and the first mark is taken once the first preparation
has finished, so a first library is not new. A work that left the library and
came back counts as new
([ADR-0103](../architecture/decisions/0103-keep-the-library-visit-on-the-server.md)).

## Counting

Work counts come from the same works and lenses as Library, so on the same
state the totals equal Library's own counts and the libraries add up to them.
Field counts come from the same fields as Attention, one per work and field.
Libraries follow the order in Settings, then any no longer configured, by name.
Work and field counts are read together from one moment of the database.

Library progress shows counts and library names only, never a title, item,
path or run. It records no event and stores nothing
([data privacy](../architecture/data-privacy.md)).
