# Write plan

Before you allow writes, Activity's permission review shows what would be
written. The plan is computed when you check write permissions and when you
download it. It is never stored, and reading it grants nothing.

## In the panel

The lead says how many changes are prepared so far, and that permission also
covers changes prepared after you allow writes. Under each field of each
library, the field's changes are grouped as chosen by you or chosen
automatically, each group with its count.

A row names the work and its authors, the current value beside the new one,
and why: your choice, or the policy setting and option that chose it, followed
by the sources that support it. An empty value reads "Not set". A cover is
named in words, "Current cover" and "Selected cover", never by its image or
identity.

Up to 25 prepared changes, every change is listed and each group starts open.
Past that, each group starts closed and shows two examples and how many more
are in the file, within 60 rows in all; a later group shows its count only.
The current value is the one the change was prepared against, not a fresh read
from Audiobookshelf.

## The file

Download CSV and Download JSON save the whole plan as one file, made at that
moment from one consistent read. A file lists at most 20,000 changes in at
most 16 MiB; when it lists fewer than are prepared, or a change could not be
read, a note inside the file says how many. Each work's changes sit together,
libraries in their configured order. Values are exact in JSON and written out
in full in CSV, where a cell a spreadsheet would read as a formula starts with
an apostrophe so it reads as text.

A large file takes time in proportion to its size and arrives as it is made;
the panel counts the changes received so far. A file that stops short, because
the connection or the application ended, is never saved as a complete one: the
panel says it was not downloaded. The page fetches the file rather than
linking to it, so an expired session or a tab on an earlier build is refused
as any read is, and the response is a `private, no-store` attachment.

Neither the panel nor the file carries a work, item or decision identity, a
path, an artifact key or a cover token. How the file is read is in
[SQLite topology](../architecture/sqlite-topology.md#activity-reads).
