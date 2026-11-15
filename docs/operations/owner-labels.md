# Owner labels

Two tools list the fields where your decision is the evidence a measurement
needs. Each decision you make on a listed field is one label. Both are
workbench tools, not part of the installed product.

## Copy the database

Stop the container and copy `/data/application.sqlite3` as
[backups and recovery](backups-and-recovery.md) describes, then start it again.
That copy keeps the container's owner (UID 65532) and mode 0600, so you may
not be able to read it. Make a second copy you can read, for example with
`sudo cp` and then `sudo chown "$USER"`, and keep it owner-only. Run each tool
from the checkout you built the install from.

## Relaxations

[ADR-0092](../architecture/decisions/0092-keep-the-automatic-decision-abstention-gate.md)
keeps fields in manual review that one of five narrow relaxations would fill
automatically. A relaxation can be switched on only on the strength of your
labels. To list the fields each one would fill:

```
uv run python scripts/relaxation_label_sample.py \
    --database /copy/application.sqlite3 \
    [--origin http://localhost:8080] [--limit 50]
```

It opens the copy read-only, writes nothing and prints, for each relaxation,
how many fields are left to decide and how many you already decided, then one
Library link per field, up to `--limit`. Opening a link selects the field on
the work's page; accept the value if it is right, otherwise enter or keep your
own. A field you already decided is left out, including one decided before a
rescan, so take a new copy to see what is left. A withdrawn decision lists
the field again.

It replays every work's decision under this checkout first, so a copy from an
install built from another checkout is refused, and so is a copy with changes
still in `application.sqlite3-wal`. A work whose decision cannot be replayed
is counted at the end and left out.

| Name in the output | Relaxation |
| --- | --- |
| Authors with one agreeing source | `authors_one_correlation` |
| Titles with two agreeing sources and no match in the book file | `title_two_correlations_without_local` |
| Publishers with two agreeing sources and no match in the book file | `publisher_two_correlations_without_local` |
| Languages with two agreeing sources and no match in the book file | `language_two_correlations_without_local` |
| Publication dates to the day with one agreeing source | `published_date_day_one_correlation` |

To read your labels, export this checkout's source and run the
[admission-rule ledger](../product/admission-rule-ledger.md) on the same copy:

```
REV=$(git rev-parse HEAD)
SCRATCH=$(mktemp -d)
git archive --format=tar --prefix=source/ "$REV" src scripts | tar -x -C "$SCRATCH"
uv run python scripts/admission_rule_ledger.py \
    --snapshot library /copy/application.sqlite3 "$REV" \
    --source-root library "$SCRATCH/source" \
    --a6-abstention --format markdown
```

The checkout must hold full history; a shallow clone is refused. The "Owner
labels" table counts, for each relaxation, the fields it would fill that you
decided: how many you accepted, entered, kept current or rejected, how often
you chose the value the relaxation would have filled, and that rate with its
Wilson 95% lower limit. A decision made before a rescan counts once, for the
library item. ADR-0092 proposes a lower limit of at least 0.95 with no wrong
value, which takes at least 73 labels without an error. The pass mark is
yours to set; the table applies none.

## Work-route offers

The [work-route measurement](work-route-measurement.md) scores Hardcover's and
Open Library's description, genres and narrators from your decisions on the
values they offer for an audiobook Audible cannot match. To list those offers:

```
uv run python scripts/work_route_offer_sample.py \
    --database /copy/application.sqlite3 \
    [--origin http://localhost:8080] [--limit 50]
```

It opens the copy read-only, writes nothing and prints, for each field, how
many offers are left to decide and how many you already decided, then one
Library link per field, up to `--limit`, with the providers that offer the
value. Only empty fields are listed. A field you already decided is left
out; a withdrawn decision lists it again. When you have decided them, run the
work-route measurement on a new copy.

## Privacy

The output of both tools is links and counts only. The copy holds your
library's records and, with settings stored in the app, its secrets: keep it
owner-only and delete it after the run.
