# Compatibility

Mechanical Librarian is pre-release. This page lists what the current build
supports and the exact versions it accepts. Support terms are in
[SUPPORT.md](../../SUPPORT.md).

## The clean-state rule

Every database, report, configuration and internal document accepts only two
states: absent, or its exact current version. This build has no migration,
import, conversion, adoption or downgrade. When a build changes a persisted
shape, the owner does a clean rebuild, and
[Upgrades](../operations/upgrades.md) says how.
[ADR-0013](../architecture/decisions/0013-pre-release-clean-schema.md) records
the rationale, and lets a future schema change ship one exact-predecessor
adoption of reconstructible derived data instead. Startup never runs it; the
owner runs it offline, and the changelog says when one ships.

- **Absent or exact.** Each store opens only when it is absent or at its
  exact current schema. An absent store is created fresh; in store mode the
  application database is created only on an empty `/data` volume, by
  first-run setup. An earlier or later schema, an extra object, altered SQL,
  an unknown vocabulary member or a failed integrity check stops startup
  without touching the file. How open-time admission works is in
  [Schema admission](../architecture/security.md#schema-admission); the stores
  themselves are in [SQLite topology](../architecture/sqlite-topology.md).
- **Documents are checked too.** Guarded-write documents (operations, plans,
  consents, actions, cleanup and events) and their matching columns are
  validated canonically before the database opens. Other stored documents are
  validated when they are read. Scalars keep their exact JSON types: a boolean
  is never accepted as a count, sequence or timestamp.
- **Nothing is silently deleted.** A refused file is left as it was. Keep it
  offline if it has value.
- **Restart and recovery are supported within the current schema.** Restart,
  replay and crash recovery work on exact-current state only. They never
  upgrade an older store.
- **The application database and artifact store are a pair.** Cover originals
  and previews in `/data/artifacts` are named by digest from
  `/data/application.sqlite3`. Never pair a current database with an older or
  unrelated artifact directory: recovery may remove artifacts the database does
  not reference. Back up and restore them together; see
  [Backups and recovery](../operations/backups-and-recovery.md).
- **Unicode is part of the schema.** Browse facts record the Unicode version
  they were folded under. A database opened by a Python with different Unicode
  data is refused and needs a clean rebuild.
- **Consent is bound to the write destinations.** A build that changes the
  destination set lapses every existing consent; see
  [Upgrades](../operations/upgrades.md#changes-that-need-owner-action-without-a-rebuild).

## Supported platforms

| Component | Supported |
| --- | --- |
| Runtime | The single non-root container built from this repository's `Dockerfile` and `compose.yaml`, as described in [Deployment](../operations/deployment.md). There is no published image. |
| Container engine | Docker Engine 25 or newer with Compose v2.20 or newer. |
| Python | 3.13, 3.14 or 3.15. The image ships Python 3.15. |
| Audiobookshelf | 2.26.0 or later, with no upper-version promise. Writes are qualified against pinned 2.26.0, 2.36.0, 2.37.1 and 2.37.2 images. Each write destination is checked for capability on the running server; a version number alone is not proof. |
| Libraries | Explicitly configured Audiobookshelf book libraries holding ebooks (EPUB) or audiobooks. Media is read through read-only mounts and never written. |
| Metadata providers | The built-in adapters listed in [Configuration](configuration.md): Hardcover, Inventaire, Open Library and Google Books for ebooks; Audible, Audnexus and AudioSilo for audiobooks; iTunes Search for ebook corroboration and audiobook covers. |
| Browser | The bundled React workspace served by the container. |

Cover reviewers and description advice are optional. Their requirements are
in [Deployment](../operations/deployment.md).

## Current versions

<!-- The identifiers on this page come from the code. After a version move, run
`uv run python scripts/compatibility_versions.py --write`; the documentation
integrity check fails until then. Descriptions name no version number. -->

These are the exact versions this build reads and writes. Any other value is
refused.

### Configuration and setup

| Identifier | What it is |
| --- | --- |
| `config-v11` | The file-mode configuration document at `/data/config.json` ([schema](config.schema.json)) |
| `review-bootstrap-v2` | The store-mode bootstrap document at `/run/secrets/review-bootstrap.json` |
| `owner-settings-v6` | Store-mode owner settings in the application database, including Book matching (`matching.work_identity`) and Current covers |
| `setup-state-v5` | Incomplete first-run setup, with libraries and their folders recorded before sources, including the setup claim token digest ([ADR-0086](../architecture/decisions/0086-deployment-held-setup-claim-token.md)). A setup left unfinished by an earlier build is refused at startup |
| `reviewer-access-v3` | Optional reviewer access for cover review and description advice |

### Reports, proposals and policy

| Identifier | What it is |
| --- | --- |
| `metadata-report-v81` | The evidence report for one item ([Metadata report](../operations/metadata-report.md)) |
| `proposal-set-v8` | Proposed values for one report ([Proposal sets](../operations/proposal-set.md)) |
| `fixed-conservative-v47` | The proposal policy |
| `closed-title-creator-publication-work-identity-v6` | The work-identity comparison |
| `metadata-policy-profile-v3` | A library's metadata policy profile |
| `review-case-v2` | A review case built from a proposal set ([Review cases](../operations/review-case.md)) |
| `level-proposal-set-v1` | The proposal set a wider Book matching level derives beside the live one, with its lineage ([Book matching levels](../operations/metadata-report.md#book-matching-levels-for-other-fields)) |
| `review-case-level-offer-v1` | The manual fields of one case a wider Book matching level substitutes, with their alternatives and lineage |
| `bibliographic-relationships-v1` | The allowed title, subtitle and series combinations inside a proposal set and review case |
| `google-books-html-visible-text-v1` | The inert visible-text projection of Google Books descriptions |
| `shadow-review-packet-v9` | The deterministic decision packet |
| `shadow-decision-set-v1` | Decisions under `deterministic-shadow-decision-policy-v27` |
| `deterministic-shadow-decision-policy-v27` | The shadow decision policy |
| `shadow-review-action-policy-v5` | Which actions a decision may take |
| `shadow-outcome-set-v11` | Automatic outcomes; action identities embed the qualification digest ([ADR-0087](../architecture/decisions/0087-operation-bound-qualification-authority.md)) |

### Decisions, covers and field effects

| Identifier | What it is |
| --- | --- |
| `human-decision-v4` | An owner decision, including `owner_entered` and `withdraw` outcomes; it binds the digest of the field offer it was made in (`offered_alternatives_digest`) and may name a closed `cause` (`matching_changed`) for the valueless row Book matching writes |
| `human-decision-event-v1`, `human-decision-ack-v1` | The decision event and its acknowledgement |
| `cover-consideration-v9` | A cover consideration, bound to its report by identity and digest and to the Book matching level it was made under; each candidate names its kind, so the original an automatic switch replaced can be put back, even one the decoder refused, which has no size or preview, and a larger copy of the current cover's own picture can replace it; it stores the would-admit note, which nothing shows |
| `cover-identity-report-v2` | Cover identity classification under one Book matching level, with the title-and-author outcome and level reasons |
| `cover-policy-v2` | The cover policy, including when a current cover may be replaced and whether an edition's cover is preferred |
| `cover-policy-report-v4` | Cover policy evaluation, embedding the cover identity report; owner readiness and automatic recommendation are independent, and the measured cover rules' outcome and reason are recorded |
| `cover-automatic-decision-v4` | An automatic cover decision, with the measured rule's reason for a replacement |
| `cover-selection-authority-v1`, `cover-selection-authority-v3` | Cover selections: v1 wraps an automatic choice, v3 an owner choice and the switch a Put back undoes |
| `field-effect-consent-state-v2` | A library consent and its basis: the owner's grant or the policy activation that carried it |
| `field-effect-consent-envelope-v1` | The consent envelope whose identity digests the destination set |
| `field-effect-plan-v1`, `field-effect-operation-v1`, `field-effect-event-v1` | Guarded write plans, operations and their append-only audit events |

### Other stores and tools

| Identifier | What it is |
| --- | --- |
| `catalogue-v2` | The catalogue database at `/data/catalogue.sqlite3` ([Catalogue](../operations/catalogue.md)) |
| `audio-inspection-v6` | The isolated audio inspection protocol |
| `epub-inspection-v3` | The isolated EPUB inspection protocol |
| `batch-manifest-v5` | Source-only foreground evidence batches ([Ebook evidence batches](../operations/ebook-evidence-batches.md)) |
| `cold-start-report-v10` | The read-only [cold-start report](../operations/cold-start-report.md) |
| `work-route-measurement-v1` | The read-only [work-route measurement](../operations/work-route-measurement.md) |
| `review-react-assets-v1` | The browser bundle identity manifest |

The session database (`/data/private/review-sessions.sqlite`) and the provider
recovery database (`/data/private/provider-recovery.sqlite`) have exact schemas
of their own and no separate version string.

### HTTP API

The owner API is served under `/api/v1`, and each response names its version
in `schema_version`. The main ones are `review-session-v1`,
`review-library-v5`, `review-preparation-v2`, `review-case-detail-v8`,
`review-case-evidence-v5`, `review-report-detail-v6`, `review-case-history-v4`,
`review-attention-v1` and `continuous-field-activity-v3`. The generated
[OpenAPI document](review-openapi-v1.json) pins each of them except
`review-session-v1`, which the server sets.

## Browser bundle identity

The container serves one React bundle. Its content-derived build identity must
match the identity packaged with the application. A missing, extra, stale,
corrupt or source-map-bearing bundle is not served; the page shows only an
owner rebuild message. An open page must send the exact identity in the
`x-ml-build-identity` header on every unsafe API request. A missing or
different identity is refused before any change, so a page left open across an
upgrade must be reloaded.

## Installed surface

The installed command is `mechanical-librarian`; its subcommands are listed in
[Deployment](../operations/deployment.md#commands-in-the-image). An automatic
outcome grants no write on its own; every write passes the same consent-gated,
guarded field-effect path as an owner decision
([Automation policy](../product/automation-policy.md)).

## Not supported

These are refused rather than interpreted:

- any earlier database, report, proposal, review, decision, outcome, consent,
  settings, setup or session state;
- configuration documents other than `config-v11` and `review-bootstrap-v2`;
- batch manifests other than `batch-manifest-v5`, subcommands not listed in
  Deployment, and any browser bundle whose identity does not match the build;
- a database paired with an artifact store it did not create.

The runtime does not offer generic model advice, model-selected writes,
reviewer queues, bulk or reversal effects, destructive operations, media
writes, arbitrary target requests, or a plugin or generic provider interface.

Some things exist only in the source tree and are not installed: catalogue,
foreground batch, report, cover-preview, proposal, review-case, deterministic
preview and contained-reviewer workbench scenarios
([Contributor workbench](../development/contributor-workbench.md)). They run
against disposable or explicitly approved private test state. Generated
OpenAPI DTOs and frontend source are build inputs. Private qualification
records are evidence formats and cannot be loaded as application state.

## After the first release

A public release will publish its own compatibility, migration and restore
promises before anyone depends on them. Post-release migration and restore is
tracked in [#57](https://github.com/atabbott/mechanical-librarian/issues/57).
