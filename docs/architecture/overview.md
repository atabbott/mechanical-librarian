# Architecture overview

The installed runtime exposes one authenticated review service. Its
owner-requested preparation path composes the application operations that
contributors may also exercise through the repository-only workbench:

```text
review-serve -> authenticated browser/API -> application services

Workbench catalogue scenario -> application RefreshCurrentCatalogue
                             -> Audiobookshelf catalogue adapter
                             -> SQLite catalogue repository

Workbench report scenario -> application GenerateMetadataReport
                    |-> Audiobookshelf current-item adapter
                    |-> isolated EPUB inspector child
                    |-> Hardcover, Open Library, and Google Books metadata adapters
                    |-> constrained cover-decoder child
                    `-> SQLite report repository + cover artifact store

Workbench batch scenario -> application manifest selection / ForegroundBatchRun
          -> current catalogue snapshot
          -> GenerateMetadataReport once per frozen eligible entry

Workbench proposal scenario -> application CreateProposalSet
                        |-> exact current report
                        |-> reused target/media/cover currentness
                        |-> pure fixed-policy evaluator
                        `-> append-only ProposalSet repository

Workbench case scenario -> application AdmitReviewCase
                      `-> exact-current ProposalSet/report/lineage recheck
                      `-> append-only immutable ReviewCase repository

Web Scan now -> application ReviewPreparationPipeline
             |-> one-library catalogue refresh
             |-> existing report, ProposalSet, and ReviewCase services
             |-> two fixed independently claimable work lanes
             `-> bounded persisted scan/work state and read-only projection

Workbench contained-reviewer scenario -> narrow source contract
                                      -> isolated source-only adapter
                                      -> bounded schema-validated JSON result
```

Bootstrap composes one authenticated `ReviewApplication` before creating
FastAPI. Named session, case, decision, Library, Attention, policy,
preparation, Activity, field-effect, recovery, and support operations share one
application serialization gate, while SQLite remains the only transaction
owner. HTTP owns routing, bounded DTO translation, and the unchanged security
boundary; it receives no repository and constructs no workflow. Critical
top-level case, Library, Attention, and preparation reads cross the interface as
bounded application view models, with evidence and history retained as separate
reads because they load independently.

Contained-reviewer authentication actions are adapter-owned and establish no
product lifecycle. The workbench is source-only, disposable by default,
excluded from the wheel and container, and has no production-target bridge.

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
observation, artifact publication, and report publication/readback. Provider
adapters own hostile record parsing, strict identity and required-link
validation, and field-local claim/evidence classification. The domain owns
closed field states, the evidence vocabulary, scope, role, source ordinal,
validation, and canonical replay. The application owns orchestration and the
exactly-once canonical merge of adapter and attempt diagnostics. The ProposalSet
seam adds exact create/get orchestration but no provider or write
port. ReviewCase and deterministic shadow-decision seams add exact-current
admission and source-validated automatic outcomes. Review preparation is one closed,
manual-triggered pipeline rather than a general-purpose queue or scheduler.
There is no central speculative ports module, generic provider registry, job
broker, polling service, or plugin scheduler. Bootstrap
explicitly composes Audiobookshelf, the isolated EPUB and cover children, the
closed Hardcover/Open Library/Google Books adapter choice, private-file credentials, SQLite,
and the private artifact store. The contained-reviewer tuple is workbench-only.

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
discovery, or generic provider registry. Provider-local fixed retry loops admit
only declared transient status and transport classes within the shared deadline,
byte envelope, pacing, and observation authority.
Google Books uses one fixed Volumes query per lane. Its volume ID remains an
edition candidate identity, work linkage is explicitly unresolved, and
bounded publication strings remain evidence-only rather than selectable dates.

`batch-manifest-v5` is an immutable secret-free operator artifact. It binds the
current catalogue generation, exact entries, report-relevant configuration,
enabled provider names and adapter discriminators, and conservative call
maxima. `ForegroundBatchRun`
serially revalidates each entry and invokes the existing report service. It
stores no attempt, progress, history, resume, retry, or lock state.

SQLite stores immutable canonical documents in `metadata_reports`, one separate
pointer per target/file scope in `current_metadata_reports`, and immutable
report-bound cover references in `metadata_report_cover_references`.
`proposal_sets` stores one content-addressed result document bound to an exact
report and closed policy version and has no current pointer. Its exact-current
table accepts only `fixed-conservative-v6`; earlier proposal-store shapes are
rejected without migration. Report insert and pointer advance share one
transaction. Proposal insertion separately rechecks the exact report and
current pointer after external currentness. The report database also stores
immutable ReviewCases with current lineages and retained deterministic shadow
decision/outcome records. The sole baseline uses
rollback-journal `DELETE` mode. The separate catalogue database stores one
exact-current `catalogue-v2` schema. The final report database, catalogue
database, configuration, artifacts, and manifest each recognize only absent or
their exact current pre-release state. Prior and intermediate schemas are
rejected before mutation; there is no migration, import, compatibility parser,
workflow, job, cache, or deletion surface. Batch work stores no attempt or
resume state. Review preparation stores only bounded mutable scan/work
progression and reconciles one current per-work row on a later scan; immutable
reports, ProposalSets, ReviewCases, decisions, and audit transitions retain
their existing authority. These rules are the current-runtime application
of repository-wide ADR-0013.
The `metadata-report-v22` document retains all 16 ordered Audiobookshelf target
destinations and their missing, null, empty, unloaded, malformed, oversized, or
value state in target currentness. It also retains the closed ordered local-field states,
source capability
provenance, and bounded occurrence/rear-spine provenance needed to replay the
observation, plus source-scoped finite provider evidence with occurrence
ordinals and canonical provider discovery lanes. Distinct ISBN-10/13 families
and the optional
complete-title/author lane remain separate; provider-local work convergence is
recorded without becoming independent corroboration. The relational
envelope remains only its scope, identity, digest, and schema-version index.
Candidate-aware domain finalization deterministically reclassifies publication
facts between claims and evidence, promotes safely linked manifestation dates,
demotes incompatible publication claims, and assigns canonical comparison
links and source ordinals. Other provider facts retain their emitted values,
while exact structural name-to-identity bindings can support aliases and
complete ordered creator-set validation. Adapters parse and bind source
relationships; they do not own comparison policy.
Within the exact current schema, a current-scope
lookup follows the separate pointer offline, while an exact-ID lookup never
changes a predecessor.

Provider observations also retain aggregate-only failure diagnostics from
adapter parser/schema classification and application attempt mapping. Their
canonical merge is persisted once and replayed without inflation. Invalid
optional values and raw process-local attempt records are not persisted.
Deterministic shadow outcomes retain their existing canonical product
contracts; no model request, result, process, attempt, or advice projection is
stored.

On supported Linux deployments, the repository retains a no-follow,
identity-checked directory chain from the filesystem root, holds an exclusive
lock on the owner-only database parent, and opens SQLite through an
unpredictable verified hard-link alias to the already-opened database inode.
SQLite journal artifacts are therefore private-alias scoped. A competing
repository process waits on that exclusive binding lock, then performs the
same identity and currentness checks after the preceding process closes; this
makes concurrent identical admissions converge without weakening the binding.
Before writes and at the final pre-commit boundary, the runtime rewalks the
current absolute chain and requires the configured path to name that inode
without recovery sidecars. Protected ancestor permissions and a dedicated
process identity make that check stable against other unprivileged identities.
Catalogue and batch manifest filesystem operations apply their own bounded
no-follow, identity-checked publication and read contracts.

Review preparation performs background read-only progression only after an
authenticated owner request. Automatic polling, policy decisions, approval,
apply, target/media writes, destructive actions, and automatic provider retry
remain unavailable.
