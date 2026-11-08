# Hardcover series sample

`scripts/hardcover_series_sample.py` lists the ebook series fields where only
Hardcover names a series and it differs from yours. These fields are not in
Attention, so this is how you find them to label for
[#1747](https://github.com/atabbott/mechanical-librarian/issues/1747). Each
series you choose is one label; the
[admission-rule ledger](../product/admission-rule-ledger.md) then reports how
often Hardcover was right. It is a workbench tool, not part of the installed
product.

Stop the container and copy `/data/application.sqlite3` as
[backups and recovery](backups-and-recovery.md) describes, then start it again.
The copy must be readable by you. Run the script from the checkout you built
the install from:

```
uv run python scripts/hardcover_series_sample.py \
    --database /copy/application.sqlite3 \
    [--origin http://localhost:8080] [--limit 50]
```

It opens the copy read-only, writes nothing and prints one Library link per
field, oldest first, up to `--limit`. Opening a link selects the work's Series
field. A field you already decided is left out, so take a new copy to see what
is left. A field Automatic kept is still listed, marked "Automatic kept your
series."; choosing a series there is still your label. A field your policy asks
about is listed and is also in Attention.

The output is links and counts only. The copy holds your library's records
and, with settings stored in the app, its secrets: keep it owner-only and
delete it after the run.
