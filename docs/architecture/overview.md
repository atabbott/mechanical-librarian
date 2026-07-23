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
insert and pointer advance share one transaction. The sole baseline uses
rollback-journal `DELETE` mode. It recognizes neither WAL/recovery sidecars nor
prior schemas, and has no migration ledger, workflow, audit, job, attempt,
cache, normalized claim table, or deletion surface.

On supported Linux deployments, the repository holds an exclusive lock on the
owner-only database parent directory and opens SQLite through an unpredictable
verified hard-link alias to the already-opened database inode. SQLite journal
artifacts are therefore private-alias scoped. The configured path must continue
to name that inode and remain free of recovery sidecars at publication.
