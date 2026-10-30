# Foreground ebook evidence batches

A batch runs the metadata-and-cover report operation over a frozen selection
of catalogue entries, one item at a time, in the foreground. It is a
contributor tool in the source-only
[contributor workbench](../development/contributor-workbench.md), not an
installed or container capability. It only produces reports. The manifest
file rules are in the
[contributor workbench](../development/contributor-workbench.md#foreground-evidence-batches).

## Steps

1. Refresh a disposable test catalogue with the `catalogue-refresh` scenario
   and check its eligible entries ([catalogue](catalogue.md)).
2. Run `batch-preview` to create a manifest.
3. Run `batch-inspect` and read the exact manifest before granting run
   authority.
4. Run `batch-run` on that manifest.

## Manifest

Preview selects either every currently eligible EPUB or an explicit ordered
list of connection, library, library ID and item ID. The two are mutually
exclusive, and a preview accepts at most 10,000 entries.

If any selected library uses an enabled `open_library` provider, preview
accepts only an explicit selection of at most five entries. Larger and
all-eligible selections fail before the manifest, provider clients,
credentials or network are touched.

The manifest path must not exist. Preview atomically writes one immutable,
canonical manifest document at the current manifest version
([current versions](../reference/compatibility.md#current-versions)). It
records:

- the scoped item identities;
- the catalogue generation and fingerprint;
- a secret-free configuration fingerprint;
- enabled provider names and adapters;
- maximum call counts.

It contains no title, author, path, credential, contact email, report body or
execution state. Its ID is the lowercase 64-character content digest. Changing,
replacing or corrupting the file invalidates it.

The maxima allow 6 Audiobookshelf item reads and 4 Audiobookshelf cover reads
per item, plus each enabled adapter's per-item limit:

| Adapter | Metadata requests | Remote cover requests |
| --- | --- | --- |
| Open Library | 49 (45 initial, 4 retries) | 4 |
| Hardcover | 27 | 20 |
| Inventaire | 27 | 4 |
| Google Books | 54 | 0 |

The report gallery admits at most four cover sources per provider and eight in
total.

## Running

`batch-run` reloads no configuration during the run. Before each report it
revalidates the current catalogue and the exact item, and binds the report to
the file identity frozen in the manifest. It does not widen series scanning or
make ranking, proposal, decision or write calls.

- Item-local extraction, format, content or inspection failures record a typed
  outcome, and the run continues.
- Configuration, credential, catalogue generation, provider, target service or
  report publication failures stop the rest of the run. Reports already
  published stay.
- An interruption stops the run.

Each enabled provider has one pacer for the whole run, which keeps its
metadata request starts at least one second apart. The Open Library pacer also
paces cover requests. Retries go through the same pacing and
deadlines.

The run ends with deterministic bounded evidence and one summary. There is no
durable retry state, resume, queue, worker, scheduler or parallelism. To
recover, create a fresh manifest and run again. Completed reports stay
available through the normal report reads.

## Authority and privacy

Preview and inspect do no report work. A run has only the authority of the
frozen selection and the report operation: current target reads, read-only
access to the selected EPUB, enabled provider lookups, bounded cover
acquisition, and local report and artifact publication.

A batch cannot create review cases, make decisions, apply changes, write target
metadata or media, or delete anything. Manifest validation fails before any
credential, network access, report publication or target inspection. Errors and
logs are value-free or redacted.

The catalogue, report database, artifact store, manifest and configuration
must each match their current versions. Earlier schemas are refused, never
migrated
([clean-state rule](../reference/compatibility.md#the-clean-state-rule)).
