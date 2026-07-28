# Foreground ebook evidence batches

The 0.9 runtime can preview and explicitly run one immutable foreground batch
over the current Audiobookshelf book catalogue. It serially reuses the complete
metadata-and-cover report operation and remains report-only.

## Prepare the catalogue

Refresh the catalogue into its own current-schema database:

```text
mechanical-librarian catalogue refresh \
  --config /run/secrets/mechanical-librarian.json \
  --database /data/catalogue.sqlite
```

Inspect eligible entries before creating a batch:

```text
mechanical-librarian catalogue list \
  --config /run/secrets/mechanical-librarian.json \
  --database /data/catalogue.sqlite \
  --filter eligible --limit 100
```

The catalogue operation is documented in
[Current book catalogue](catalogue.md).

## Create and inspect a manifest

Preview all currently eligible EPUBs:

```text
mechanical-librarian batch preview \
  --config /run/secrets/mechanical-librarian.json \
  --catalogue /data/catalogue.sqlite \
  --manifest /data/batches/ebook-batch.json \
  --all-eligible
```

Alternatively, repeat `--select CONNECTION LIBRARY LIBRARY_ID ITEM_ID` for an
explicit ordered set. A preview accepts at most 10,000 entries. Explicit
selection and `--all-eligible` are mutually exclusive.

When any selected library references an enabled `open_library` provider,
preview accepts only an explicit selection of at most five entries.
`--all-eligible` and larger explicit selections fail before manifest creation,
provider-client construction, credential access, or egress. Existing larger and
all-eligible batches remain available when Open Library is disabled.

The manifest path must be absent. Preview atomically creates one immutable
canonical `batch-manifest-v4` document. It records scoped identities, the
catalogue generation and fingerprint, a secret-free configuration fingerprint,
enabled provider names and adapter discriminators, and conservative call-count
maxima. Each enabled Open Library adapter allows at most 45 metadata requests
and four remote cover requests per selected item: up to nine Search lanes and
two editions plus two distinct works per lane. Hardcover permits at most nine
metadata requests. The report gallery admits at most four
cover sources per provider and eight overall. The maxima add the enabled
adapters without a generic cost or scheduling framework.
Each enabled Google Books adapter permits at most nine metadata requests and
no cover requests per selected item.
The manifest contains no title, author,
path, credential, operational contact email, report body, execution state, or
history.

Inspect the exact document before granting run authority:

```text
mechanical-librarian batch inspect \
  --manifest /data/batches/ebook-batch.json
```

The manifest ID is the lowercase 64-character content digest in that output.
Changing, replacing, or corrupting the file invalidates it.

## Run the batch

Run the exact inspected manifest:

```text
mechanical-librarian batch run \
  --config /run/secrets/mechanical-librarian.json \
  --catalogue /data/catalogue.sqlite \
  --manifest /data/batches/ebook-batch.json \
  --manifest-id <64-lowercase-hex-characters> \
  --database /data/reports.sqlite \
  --artifact-store /data/cover-artifacts
```

The command runs serially in the foreground and streams deterministic JSON
lines followed by one final summary. It reloads no configuration during the
run, revalidates the current catalogue and exact item before each report, and
binds every report to the file identity frozen in the manifest.

Item-local extraction, format, content, or inspection failures emit a typed
outcome and continue. Configuration, credential, catalogue-generation,
provider, target-service, or report-publication failures stop the remainder
while preserving already published reports. Interruption propagates to the
operator.

One Open Library pacer is shared by the whole serial foreground run. It keeps
all Open Library metadata and cover request starts at least one second apart;
requests remain single-attempt and no retry or backoff state is created.

There is no attempt record, resume, automatic retry, backoff, queue, worker,
lease, scheduler, parallel execution, or report adoption. Recovery is an
operator-created fresh manifest and foreground run. Existing completed reports
remain available through the normal exact-ID and current-scope reopen commands.

## Authority and privacy

Preview and inspect perform no metadata report work. Run has only the authority
already granted to the frozen catalogue selection and the configured report
operation: current target reads, selected read-only EPUB access, explicitly
enabled provider observations, bounded cover acquisition, and local report and
artifact publication.

The batch cannot create review cases, make decisions, apply changes, write
target metadata, write media, or delete state. Errors and logs remain
value-free or redacted. Manifest validation fails before credentials, egress,
report publication, or target/media inspection.

The catalogue database, report database, artifact store, manifest, and
configuration must each satisfy their exact current pre-release contracts.
Earlier or intermediate schemas are rejected rather than migrated or
interpreted.
