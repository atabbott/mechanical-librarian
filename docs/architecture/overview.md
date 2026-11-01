# Architecture overview

Mechanical Librarian runs as one container with one Python process. That
process serves the owner's browser workspace and runs all background work.
State lives in SQLite databases and a cover artifact store under `/data`.
Media is mounted read-only; the only writes go to Audiobookshelf through its
supported API. What the product does and the boundaries it keeps are in the
[README](../../README.md) and [PRODUCT.md](../../PRODUCT.md); why it is one
container is [ADR-0001](decisions/0001-single-container.md).

The authorities this page summarises are:

- `src/mechanical_librarian/bootstrap/composition.py`, the composition root,
  for what is wired together;
- `tests/architecture/test_import_boundaries.py` for the layering rule;
- `tests/architecture/test_bootstrap_ratchet.py` for what bootstrap may hold;
- the [ADRs](decisions/README.md) for why each boundary is where it is.

## Layers

Production code under `src/mechanical_librarian/` has six layers.

| Layer | Owns | May import |
| --- | --- | --- |
| `domain` | Closed, immutable value types and pure rules: reports, ProposalSets, review cases, decisions, policy, field-effect plans. No I/O. | `domain` |
| `application` | Operations, orchestration, admission gates and the narrow contracts (protocols) each operation needs | `application`, `domain` |
| `adapters` | Audiobookshelf, metadata providers, isolated media inspection, AI reviewers, SQLite persistence, the artifact store | `adapters`, `application`, `domain` |
| `infrastructure` | Configuration, settings authority, first boot, local media, TLS and credentials | `infrastructure`, `application`, `domain` |
| `interfaces` | The FastAPI web app, React asset serving, the CLI | `interfaces`, `application`, `domain` |
| `bootstrap` | The composition root, the container entry point and child-process entry points | everything |

```text
interfaces -> application -> domain
adapters --------------------^
infrastructure -> application/domain contracts
bootstrap composes all layers
```

Only bootstrap chooses concrete adapters
([ADR-0006](decisions/0006-port-adapters-and-composition-root.md)). Each
contract lives beside the application operation that uses it; there is no
central ports module, provider registry, job broker or plugin scheduler. The
contract families are listed in
[application service contracts](service-port-contracts.md).

Bootstrap holds wiring and entry points, plus a pinned residual of older
behaviour. The bootstrap ratchet fails unless every growth
in it (module size, def count, a residual span, a new behaviour-bearing class,
a def over 60 lines, a new module) is an edit to the ratchet's constants in
the same pull request, and stops composition once it has grown past its review band
since the owner's last review. The test makes growth visible; whether a raise is
acceptable is decided in review under the rule in
[AGENTS.md](../../AGENTS.md#code-economy).

Provider adapters parse and validate hostile records, bind identities and
classify each field. They do not own comparison policy: the domain finalises
each candidate against the local claims (agreement links, publication-date
promotion or demotion, source ordinals), and the application merges
diagnostics once.

The repository-only [contributor workbench](../development/contributor-workbench.md)
in `workbench/` drives the same application operations against disposable
state. It is excluded from the wheel and the container and cannot target a
production deployment
([ADR-0040](decisions/0040-source-only-contributor-workbench.md)).

## Runtime

### The container process

`mechanical-librarian-container` checks the `/data` volume, selects the
first-boot mode (guided setup, resumed setup, store mode or file mode;
[ADR-0058](decisions/0058-settings-authority-and-first-boot.md)), creates the
private reviewer directories and starts the review server on port 8443.

The review server is one Uvicorn listener. Until setup completes it serves
only the setup flow. After that each request runs against one runtime
generation; a saved Settings change prepares a new generation and swaps it in
for the next request, or keeps the old one if preparation fails.

Background work runs as threads in the same process:

- **Review preparation.** Two fixed lanes scan each configured library on a
  schedule and on the owner's request, prepare each work's review package,
  cover considerations and description advice, and drive provider recovery
  ([preparation](../operations/review-case.md#preparation)).
- **Guarded application.** One loop claims and executes guarded field effects
  and their cleanup obligations. A decision or preparation event wakes it at
  once; otherwise it checks every two seconds.
- **Activity projection.** A projector keeps the in-memory Activity facts
  current.

Untrusted bytes are handled in short-lived child processes with fixed resource
limits: the EPUB inspector, the audio probe and its interpreter, the cover
image decoder and the sandboxed Codex reviewer. A child receives descriptors
and bounded inputs only; the Codex reviewer also gets its own read-only
sign-in credential. The parent keeps every target and provider credential,
network connection, persistence and currentness decision
([ADR-0011](decisions/0011-isolated-epub-inspector.md),
[ADR-0063](decisions/0063-isolated-audiobook-inspector.md)).

The `mechanical-librarian` command offers the same composed operations for
operators: `review-serve`, `policy-preview`, `cover-review`, `review-activity`
and `provider-recovery`. Catalogue refresh, single reports and evidence
batches are workbench scenarios, not installed commands.

### Serialisation and storage

The web layer receives one composed `ReviewApplication`, never a repository.
Its named operations (sessions, cases, decisions, Library, Attention, policy,
preparation, field effects, recovery, Activity, support) pass through one
admission gate that separates short foreground work from background work.
SQLite is the only transaction owner. Network, media and filesystem work
happens outside write transactions, so a slow provider never holds the
writer lock. Library and Attention read through their own borrowed read
connection; Activity reads its own projection
([ADR-0077](decisions/0077-owned-responsive-review-reads.md)).

Library and Attention rows are built from bounded facts
(`domain/review_browse.py`). Each row checks its documents' content addresses
and validates every value it shows under that field's canonical rule, so a
shown value matches what the writer validated. A row does not re-check
cross-field consistency or parts it does not show. Work detail and every write
path parse the full document.

Each store accepts only absence or its exact current schema; nothing is
migrated or repaired
([ADR-0013](decisions/0013-pre-release-clean-schema.md)). The databases, their
file binding, transactions, crash recovery and caches are in
[SQLite topology](sqlite-topology.md).

### Browser workspace

The workspace destinations, Library (with Attention and work detail), Policy,
Activity and Settings, share one React shell built from the exact OpenAPI
document; `/login` is server-rendered. React owns presentation state,
TanStack Router owns route and search state, and TanStack Query owns server
reads. One HTTP module in `frontend/src/api/` maps transport types and
handles CSRF renewal, build identity and expired sessions. A server-sent
stream carries only invalidations (topic, optional case identity, sequence);
the page then refetches over HTTP. FastAPI is the only server and the only
authority. Node exists only in build stages; the image ships the built
assets, not frontend source
([ADR-0043](decisions/0043-reproducible-react-browser-platform.md),
[ADR-0044](decisions/0044-activate-persistent-react-workspace.md)).

## Data flow

Each stage produces an immutable, content-addressed record and checks the one
before it is still current. Nothing earlier than a decision carries write
authority.

```text
Audiobookshelf ─► catalogue ─► preparation (per work)
                                 │  media inspection + provider lanes
                                 ▼
                           MetadataReport ─► ProposalSet ─► ReviewCase
                                                              │
                                   Library, Attention ◄───────┤
                                                              ▼
                         owner decision or qualified automatic decision
                                                              │
                                                              ▼
                   guarded field effect: consent, backup, pre-read,
                   one write, read-back, Activity ─► Audiobookshelf API
```

1. **Inventory.** Preparation refreshes a bounded catalogue of every
   configured book library (at most 10,000 entries) through Audiobookshelf's
   read API and publishes it as one generation
   ([catalogue](../operations/catalogue.md)).
2. **Evidence.** For each eligible work, an isolated child reads the EPUB or
   the audio files, and the enabled metadata providers its library references
   are queried through fixed lanes
   ([dispatch](../reference/configuration.md#dispatch)). Provider results are
   neutral: every valid candidate is kept, with no ranking. A lane that still
   fails after its bounded in-request retries is retried later by
   [provider recovery](../operations/provider-recovery.md).
3. **Report.** The target's 16 destinations, the local evidence, provider
   observations and cover outcomes are published as one immutable
   [metadata report](../operations/metadata-report.md), with a current pointer
   per target and file.
4. **Proposals.** The fixed built-in policy, with the owner's resolved policy,
   evaluates the exact report into a
   [ProposalSet](../operations/proposal-set.md): one result per destination.
   It makes no provider call.
5. **Review case.** An actionable ProposalSet is admitted as an immutable
   [review case](../operations/review-case.md) for the exact current target.
   The report, ProposalSet, case and its Library projection publish together
   as one review package; a refresh keeps the previous package visible until
   its successor replaces it.
6. **Decision.** The owner decides a field in the workspace, or a qualified
   Automatic policy option records an automatic decision
   ([automation policy](../product/automation-policy.md),
   [ADR-0036](decisions/0036-qualified-decision-only-automatic-policy.md)).
   Cover considerations and description advice add advisory evidence first
   ([AI trust boundary](ai-boundary.md)).
7. **Guarded effect.** A decision becomes a field-effect plan. Each claim
   rechecks consent, backup readiness, capability, currentness and the
   target's pre-state, sends one minimal request, reads the value back and
   records the outcome in Activity
   ([ADR-0037](decisions/0037-origin-neutral-guarded-field-effects.md),
   [security](security.md#consent-and-guarded-effects)).

The records each stage produces are described in the
[domain model](domain-model.md); what they retain is in
[data privacy](data-privacy.md).
