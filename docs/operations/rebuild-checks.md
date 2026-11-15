# Rebuild checks

`scripts/rebuild_checks.py` runs the read-only workbench checks over one
folder of copies and saves each result in a new owner-only folder: the
[cold-start report](cold-start-report.md), the
[work-route measurement](work-route-measurement.md) and the
[Hardcover series sample](hardcover-series-sample.md). It works whether the
install keeps its settings in `config.json` or saved in the app. It is a
workbench tool, not part of the installed product.

## The copies folder

Put the copies in one folder you own, named as they are in the install:

| Name | Copied from | Needed |
| --- | --- | --- |
| `application.sqlite3` | `/data/application.sqlite3` | Always. |
| `catalogue.sqlite3` | `/data/catalogue.sqlite3` | For the cold-start report's catalogue figures. Without it they are `null`. |
| `config.json` | `/data/config.json` | When the install has one. It then decides the settings. |
| `review-bootstrap.json` | `review-bootstrap.json` beside `compose.yaml` | When settings are saved in the app. |
| `provider-recovery.sqlite` | `/data/private/provider-recovery.sqlite` | For the cold-start report's recovery figures. Without it they are `null`. |

The files in `/data` belong to the container's user (UID 65532) with mode
0600, and so does `review-bootstrap.json`. With Docker Desktop the volume is
inside its virtual machine, not on your disk. So copy them through a helper
container, which also hands the copies to you.

First find the volume name, or the folder, mounted at `/data`. While the app
is running, from the folder that holds `compose.yaml`:

```
docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}}{{end}}{{end}}' "$(docker compose ps -q mechanical-librarian)"
```

Stop the app with `docker compose down` (without `-v`) so the databases are
complete, then, from the same folder, copy the files into a folder outside
the checkout (here `~/ml-copies`), so no copy can end up in a commit, replacing
`<volume-or-folder>` with what the command above printed:

```
mkdir -m 700 ~/ml-copies
docker run --rm -v <volume-or-folder>:/data:ro -v "$PWD/review-bootstrap.json":/in/review-bootstrap.json:ro -v ~/ml-copies:/out debian:bookworm-slim sh -c "cp /data/application.sqlite3 /data/catalogue.sqlite3 /out/; cp /data/config.json /out/ 2>/dev/null; cp /in/review-bootstrap.json /out/; cp /data/private/provider-recovery.sqlite /out/ 2>/dev/null; chown -R $(id -u):$(id -g) /out && chmod 600 /out/*"
```

Start the app again with `docker compose up -d`. If there is no
`review-bootstrap.json` beside `compose.yaml`, leave out its `-v` option and
`cp /in/review-bootstrap.json /out/;`, or Docker creates an empty folder of
that name.

Copies taken while the app runs can disagree with each other, so always stop
it first.

## Running it

Run it from the checkout the install was built from, and give it a new folder
for the results:

```
uv run python scripts/rebuild_checks.py \
    --copies ~/ml-copies --output ~/ml-results \
    [--mount-root books=/host/path/to/mount ...] [--limit 50]
```

| File | Check |
| --- | --- |
| `cold-start-report.json` | The cold-start report, read with `config.json` when the copies hold it, otherwise with `review-bootstrap.json`. |
| `work-route-measurement.json` | The work-route measurement. |
| `work-route-provenance.json` | The work-route measurement's provenance fragment (`--provenance`). |
| `hardcover-series-sample.txt` | The Hardcover series sample, with links at the address the install's own settings file records. |
| `partial-author-replay.json` | The partial-author replay, when the checkout has `scripts/partial_author_replay.py`. Otherwise it is skipped, and a skip is not a failure. |
| `summary.txt` | The lines it printed. |

- `--mount-root NAME=PATH` (optional, repeatable) maps a media mount to a
  folder on this computer, for the cold-start report's file counts. Without
  it the report walks the container path.
- `--limit` (optional) is how many Hardcover links to list, from 1 to 500
  (default 50).

The output folder must not exist yet; it is created with mode 0700 and each
file with mode 0600, so an earlier run is never overwritten.

It prints one line per check. One failing check does not stop the others,
and leaves no file: its line says why. Copies with neither `config.json` nor
`review-bootstrap.json` still give both work-route files. The last line says
whether every check finished, and the exit status is 0 if so and 1 if not.

No check uses the network, and none changes a copy. Nothing is written
outside the output folder, except that reading the catalogue copy can leave
`catalogue.sqlite3-shm` and `catalogue.sqlite3-wal` beside it.

## Privacy

The results hold names from your settings, closed codes, counts and Library
links, never a title, author, path or saved key. The copies hold your library's
records and, with settings saved in the app, its keys: keep the copies folder
owner-only and delete it after the run.
