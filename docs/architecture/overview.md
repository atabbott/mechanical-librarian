# Architecture overview

The 0.4 runtime is one synchronous vertical path:

```text
CLI -> application GenerateMetadataReport -> domain MetadataReport
                    |-> Audiobookshelf current-item adapter
                    |-> isolated EPUB inspector child
                    |-> concrete Hardcover provider adapter
                    `-> SQLite report repository
```

Dependency direction remains:

```text
interfaces -> application -> domain
adapters --------------------^
infrastructure -> application/domain contracts
bootstrap composes all layers
```

The application service owns four narrow contracts: scoped target reading,
EPUB inspection/currentness, one-provider observation, and report
publication/readback. There is no central speculative ports module or generic
provider registry. Bootstrap explicitly composes Audiobookshelf, the isolated
EPUB inspector, Hardcover, private-file credentials, and SQLite.

The target adapter reads only `/api/items/{id}?expanded=1`; it persists no
inventory. The EPUB child owns its package model, opens one archive once, and
returns only bounded opening facts. The parent retains all credentials,
network, matching, persistence, and currentness authority. The provider adapter
selects only the ISBN inputs supported by its fixed one-request operation and
retains every valid candidate without ranking.

SQLite stores immutable canonical documents in `metadata_reports` and one
separate pointer per target/file scope in `current_metadata_reports`. Report
insert and pointer advance share one transaction. No migration ledger,
workflow, audit, job, attempt, cache, normalized claim table, or deletion
surface exists.
