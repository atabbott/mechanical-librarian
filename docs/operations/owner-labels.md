# Owner labels

Two tools list the fields where your decision is the evidence a measurement
needs. Each decision you make on a listed field is one label. Both are
workbench tools, not part of the installed product. Run them from the checkout
you built the install from.

## Copy the database

The tools read a copy of the application database, never the live one. From
the folder that holds `compose.yaml`:

```
docker compose stop mechanical-librarian
mkdir -p -m 700 ~/ml-labels
docker cp "$(docker compose ps -aq mechanical-librarian)":/data/application.sqlite3 ~/ml-labels/
docker compose start mechanical-librarian
COPY=~/ml-labels/application.sqlite3
```

Stopping the container first lets it write every change into
`application.sqlite3`. The copy belongs to you, so the tools can read it. It
holds your library's records and, with settings stored in the app, its
secrets: keep it owner-only and delete it after the run.

## Relaxations

[ADR-0092](../architecture/decisions/0092-keep-the-automatic-decision-abstention-gate.md)
keeps fields in manual review that one of five narrow relaxations would fill
automatically. A relaxation can be switched on only on the strength of your
labels. To list the fields each one would fill:

```
uv run python scripts/relaxation_label_sample.py --database "$COPY"
```

It opens the copy read-only and writes nothing. For each relaxation it prints
how many fields are left to decide and how many you already decided. Then it
prints one Library link per field, up to 50. Opening a link selects the field
on the work's page: choose the right value, or enter your own. A field you
already decided is left out, including one decided before a rescan. A
withdrawn decision lists the field again. Take a new copy to see what is left.

It replays every work's decision under this checkout first. A copy from an
install with other decision rules is refused, and so is a copy with changes
still in `application.sqlite3-wal`. A work that could not be checked is
counted at the end and left out.

| Name in the output | Relaxation |
| --- | --- |
| Authors with one agreeing source | `authors_one_correlation` |
| Titles with two agreeing sources and no match in the book file | `title_two_correlations_without_local` |
| Publishers with two agreeing sources and no match in the book file | `publisher_two_correlations_without_local` |
| Languages with two agreeing sources and no match in the book file | `language_two_correlations_without_local` |
| Publication dates to the day with one agreeing source | `published_date_day_one_correlation` |

When you have decided them, take a new copy and read your labels with the
[admission-rule ledger](../product/admission-rule-ledger.md):

```
REV=$(git rev-parse HEAD)
SCRATCH=$(mktemp -d)
git archive --format=tar --prefix=source/ "$REV" src scripts | tar -x -C "$SCRATCH"
uv run python scripts/admission_rule_ledger.py \
    --snapshot library "$COPY" "$REV" \
    --source-root library "$SCRATCH/source" \
    --a6-abstention --format markdown
```

The checkout must hold full history. A shallow clone is refused, and
`git fetch --unshallow` fills it in. The "Owner labels" table has a row for
each relaxation, counting the fields you decided. It shows how many you
accepted, entered, kept current or rejected. It also shows how often you chose
the value the relaxation would have filled, with that rate's Wilson 95% lower
limit. A decision made before a rescan counts once. ADR-0092 proposes a lower
limit of at least 0.95, which takes at least 73 labels without an error. The
pass mark is yours to set. The table applies none.

## Work-route offers

The [work-route measurement](work-route-measurement.md) scores Hardcover's and
Open Library's description, genres and narrators from your decisions on the
values they offer for an audiobook Audible cannot match. To list those offers:

```
uv run python scripts/work_route_offer_sample.py --database "$COPY"
```

It opens the copy read-only and writes nothing. For each field it prints how
many offers are left to decide and how many you already decided. Then it
prints one Library link per field, up to 50, with the providers that offer the
value. Only empty fields are listed. A field you already decided is left out.
A withdrawn decision lists it again. When you have decided them, take a new
copy and run the work-route measurement on it.

## Options

Both tools print links and counts only. Add `--origin https://host:port` if
you open Mechanical Librarian at another address than http://localhost:8080,
and `--limit N` (1 to 500) for more than 50 links per list.
