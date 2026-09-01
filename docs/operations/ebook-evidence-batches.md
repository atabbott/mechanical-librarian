# Foreground ebook evidence batches

The source-only contributor workbench can preview and explicitly run one
immutable foreground batch over a disposable current Audiobookshelf book
catalogue. This is not an installed runtime or container capability. It
serially reuses the complete metadata-and-cover report operation and remains
report-only. See [Contributor workbench](../development/contributor-workbench.md).

## Prepare the catalogue

Use the typed catalogue-refresh scenario against an explicitly supplied test
workspace, then inspect eligible entries before creating a batch.

The catalogue operation is documented in
[Current book catalogue](catalogue.md).

## Create and inspect a manifest

Preview either all currently eligible EPUBs or an explicit ordered tuple of
connection, library, library ID, and item ID. A preview accepts at most 10,000
entries. Explicit selection and all-eligible selection are mutually exclusive.

When any selected library references an enabled `open_library` provider,
preview accepts only an explicit selection of at most five entries.
All-eligible and larger explicit selections fail before manifest creation,
provider-client construction, credential access, or egress. Existing larger and
all-eligible batches remain available when Open Library is disabled.

The manifest path must be absent. Preview atomically creates one immutable
canonical `batch-manifest-v5` document. It records scoped identities, the
catalogue generation and fingerprint, a secret-free configuration fingerprint,
enabled provider names and adapter discriminators, and conservative call-count
maxima. Each enabled Open Library adapter allows at most 49 metadata requests
and four remote cover requests per selected item: up to 45 initial Search and
detail requests plus four bounded retries. Hardcover permits at most 27
metadata requests, Inventaire at most 27 metadata requests and four remote cover
requests, and Google Books at most 54 metadata requests. The report gallery
admits at most four cover sources per provider and eight overall. The maxima add
the enabled adapters without a generic cost or scheduling framework.
Each enabled Google Books adapter permits at most 54 metadata requests and
no cover requests per selected item.
The manifest contains no title, author,
path, credential, operational contact email, report body, execution state, or
history.

Inspect the exact document before granting run authority.

The manifest ID is the lowercase 64-character content digest in that output.
Changing, replacing, or corrupting the file invalidates it.

## Run the batch

The typed batch-run scenario runs the exact inspected manifest serially in the
foreground and returns deterministic bounded evidence
followed by one final summary. It reloads no configuration during the
run, revalidates the current catalogue and exact item before each report, and
binds every report to the file identity frozen in the manifest.
Series evidence remains the isolated child's bounded explicit package or
front/rear indicia projection; batch execution does not expand scan scope or
make series ranking, proposal, decision, or write calls.

Item-local extraction, format, content, or inspection failures emit a typed
outcome and continue. Configuration, credential, catalogue-generation,
provider, target-service, or report-publication failures stop the remainder
while preserving already published reports. Interruption propagates to the
operator.

One Open Library pacer and one Hardcover pacer are shared by the whole serial
foreground run. They keep their provider's metadata request starts at least one
second apart; the Open Library pacer also governs its cover requests. Initial
attempts and bounded retries traverse the same pacing and deadline gates.

Bounded aggregate-safe attempt diagnostics exist only in the provider process.
There is no durable retry or attempt state, resume, background retry, queue,
worker, lease, scheduler, parallel execution, or report adoption. Recovery is
an operator-created fresh manifest and foreground run. Existing completed
reports remain available through the normal exact-ID and current-scope reopen
operations.

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
