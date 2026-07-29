# Architecture overview

The 0.10 development runtime exposes synchronous CLI command families:

```text
CLI catalogue -> application RefreshCurrentCatalogue
              -> Audiobookshelf catalogue adapter
              -> SQLite catalogue repository

CLI metadata-report -> application GenerateMetadataReport
                    |-> Audiobookshelf current-item adapter
                    |-> isolated EPUB inspector child
                    |-> Hardcover, Open Library, and Google Books metadata adapters
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

CLI shadow-review-preview -> application preview
                          -> profile bytes + exact-current ReviewCase lineage
                          -> no reviewer construction or egress

CLI shadow-review-inspect -> application inspect
                          -> exact persisted request, including historical
                             `current: false` state
                          -> no reviewer construction or egress

CLI shadow-review-request -> application execution
                          |-> exact-current case/report/proposal/profile/packet
                          |-> append-only request/attempt/dispatch/outcome records
                          `-> isolated Codex reviewer for one request only

CLI shadow-review-reevaluate -> application explicit reevaluation
                             |-> latest-attempt and process-absence checks
                             |-> append-only successor attempt records
                             `-> isolated Codex reviewer for one request only

CLI reviewer-auth-* -> bootstrap profile load -> CodexShadowReviewer.auth
```

Reviewer authentication is deliberately bootstrap-owned; it is separate from
the application-owned shadow-review execution and persistence contracts.

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
port. ReviewCase and shadow-review seams add exact-current admission and
qualification-only execution/persistence. There is no central speculative ports
module, generic provider registry, queue, worker, or scheduler. Bootstrap
explicitly composes Audiobookshelf, the isolated EPUB and cover children, the
closed Hardcover/Open Library/Google Books adapter choice, private-file credentials, SQLite,
the private artifact store, and the external reviewer tuple.

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
table accepts only `fixed-conservative-v3`; earlier proposal-store shapes are
rejected without migration. Report insert and pointer advance share one
transaction. Proposal insertion separately rechecks the exact report and
current pointer after external currentness. The report database also stores
immutable ReviewCases with current lineages and append-only qualification-only
shadow request, attempt, dispatch, and outcome records. The sole baseline uses
rollback-journal `DELETE` mode. The separate catalogue database stores one
exact-current `catalogue-v1` schema. The final report database, catalogue
database, configuration, artifacts, and manifest each recognize only absent or
their exact current pre-release state. Prior and intermediate schemas are
rejected before mutation; there is no migration, import, compatibility parser,
workflow, job, cache, or deletion surface. Batch work stores no attempt or
resume state; shadow-review attempts are the explicit qualification-only
exception. These rules are the current-runtime application of repository-wide
ADR-0013.
The `metadata-report-v21` document retains all 16 ordered Audiobookshelf target
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
Candidate-aware domain finalization adds only deterministic comparison links:
original provider claims and evidence remain unchanged, while exact structural
name-to-identity bindings can support aliases and complete ordered creator-set
validation. Adapters parse and bind source relationships; they do not own
comparison policy.
Within the exact current schema, a current-scope
lookup follows the separate pointer offline, while an exact-ID lookup never
changes a predecessor.

Provider observations also retain aggregate-only failure diagnostics from
adapter parser/schema classification and application attempt mapping. Their
canonical merge is persisted once and replayed without inflation. Invalid
optional values and raw process-local attempt records are not persisted.
Shadow outcomes store the same canonical bounded diagnostic object, while
`shadow-review-inspect-v2` projects only case/request identities, aggregate
attempt count, currentness, status, and canonical diagnostics; it does not
expose request/packet/result content or process and attempt identities.

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

Normal activation, human or AI decision, approval, apply, target/media write,
background execution, and automatic retry remain unavailable.
