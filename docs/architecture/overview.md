# Architecture overview

The 0.10 development runtime has five synchronous CLI paths:

```text
CLI catalogue -> application RefreshCurrentCatalogue
              -> Audiobookshelf catalogue adapter
              -> SQLite catalogue repository

CLI metadata-report -> application GenerateMetadataReport
                    |-> Audiobookshelf current-item adapter
                    |-> isolated EPUB inspector child
                    |-> Hardcover and Open Library metadata/cover adapters
                    |-> constrained cover-decoder child
                    `-> SQLite report repository + cover artifact store

CLI batch -> application manifest selection / ForegroundBatchRun
          -> current catalogue snapshot
          -> GenerateMetadataReport once per frozen eligible entry

CLI proposal-set-create -> application CreateProposalSet
                        |-> exact current report
                        |-> reused target/media/cover currentness
                        |-> pure fixed-policy evaluator
                        `-> append-only ProposalSet repository

CLI review-case-admit -> application AdmitReviewCase
                      `-> exact-current ProposalSet/report/lineage recheck
                      `-> append-only immutable ReviewCase repository
```

Dependency direction remains:

```text
interfaces -> application -> domain
adapters --------------------^
infrastructure -> application/domain contracts
bootstrap composes all layers
```

The application layer owns narrow contracts for current catalogue observation,
catalogue persistence, frozen manifest selection, serial foreground execution,
scoped target reading, EPUB inspection/currentness, provider and cover
observation, artifact publication, and report publication/readback. The
ProposalSet seam adds exact create/get orchestration but no provider or write
port. There is no central speculative ports module, generic provider registry,
queue, worker, or scheduler. Bootstrap explicitly composes Audiobookshelf, the
isolated EPUB and cover children, the closed Hardcover/Open Library adapter
choice, private-file credentials, SQLite, and the private artifact store.

The catalogue adapter uses bounded Audiobookshelf library, library-items, and
item-detail GET routes. It atomically publishes one current generation of at
most 10,000 entries across configured book libraries. Catalogue state contains
bounded identity, title/author display, ebook/audio facets, eligibility, and a
singular supported EPUB projection; list output never exposes a path.

The scoped report adapter reads one item detail and selected file. The EPUB
child owns package, opening-matter, and bounded rear-spine evidence passes,
opens one immutable archive snapshot once, and returns only bounded facts. The
cover child validates bounded JPEG, PNG, or static WebP bytes without network
authority. The parent retains all credentials, network, matching, persistence,
artifact, and currentness authority. Each provider adapter selects only the ISBN
inputs supported by its fixed operation and retains every valid candidate
without ranking. Open Library uses exact Search-to-edition-to-work admission,
one adapter-local request-start pacer, and no author traversal, fallback
discovery, retry, or generic provider registry.

`batch-manifest-v2` is an immutable secret-free operator artifact. It binds the
current catalogue generation, exact entries, report-relevant configuration,
enabled provider names and adapter discriminators, and conservative call
maxima. `ForegroundBatchRun`
serially revalidates each entry and invokes the existing report service. It
stores no attempt, progress, history, resume, retry, or lock state.

SQLite stores immutable canonical documents in `metadata_reports`, one separate
pointer per target/file scope in `current_metadata_reports`, and immutable
report-bound cover references in `metadata_report_cover_references`.
`proposal_sets` stores one content-addressed result document bound to an exact
report and has no current pointer. Report insert and pointer advance share one
transaction. Proposal insertion separately rechecks the exact report and
current pointer after external currentness. The sole baseline uses
rollback-journal `DELETE` mode. The separate catalogue database stores one
exact-current `catalogue-v1` schema. The final report database, catalogue
database, configuration, artifacts, and manifest each recognize only absent or
their exact current pre-release state. Prior and intermediate schemas are
rejected before mutation; there is no migration, import, compatibility parser,
workflow, job, attempt, cache, or deletion surface. These rules are the
current-runtime application of repository-wide ADR-0013.
The `metadata-report-v8` document retains all 16 ordered Audiobookshelf target
destinations and their missing, null, empty, unloaded, malformed, oversized, or
value state in target currentness. It also retains the closed ordered local-field states,
source capability
provenance, and bounded occurrence/rear-spine provenance needed to replay the
observation, plus source-scoped finite provider evidence; the relational
envelope remains only its scope, identity, digest, and schema-version index.
Within the exact current schema, a current-scope
lookup follows the separate pointer offline, while an exact-ID lookup never
changes a predecessor.

On supported Linux deployments, the repository retains a no-follow,
identity-checked directory chain from the filesystem root, holds an exclusive
lock on the owner-only database parent, and opens SQLite through an
unpredictable verified hard-link alias to the already-opened database inode.
SQLite journal artifacts are therefore private-alias scoped. Before writes and
at the final pre-commit boundary, the runtime rewalks the current absolute
chain and requires the configured path to name that inode without recovery
sidecars. Protected ancestor permissions and a dedicated process identity make
that check stable against other unprivileged identities. Catalogue and batch
manifest filesystem operations apply their own bounded no-follow,
identity-checked publication and read contracts.
