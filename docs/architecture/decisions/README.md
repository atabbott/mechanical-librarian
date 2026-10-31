# Architecture Decision Records

Each ADR records one durable decision and why it was made. Status is
`proposed` (not binding), `accepted` (binding), `superseded`, `deprecated`
(still in force, due for removal) or `rejected`. Start new decisions from
[the template](template.md). Never rewrite an accepted ADR to match a later
preference; supersede or amend it.

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-single-container.md) | accepted | Ship one self-contained container |
| [0002](0002-audiobookshelf-first.md) | accepted | Build the first adapter for Audiobookshelf |
| [0003](0003-sqlite-default.md) | superseded by 0012; schema rules by 0013 | Use SQLite as the default state store |
| [0004](0004-ai-is-untrusted.md) | accepted | Treat AI as an untrusted reviewer |
| [0005](0005-agpl-3-or-later.md) | accepted | License the project under AGPL-3.0-or-later |
| [0006](0006-port-adapters-and-composition-root.md) | accepted; migration rules superseded by 0012 and 0013 | Separate port adapters and use one composition root |
| [0007](0007-transient-epub-package-child.md) | superseded by 0011 | Extract EPUB packages in a transient child |
| [0008](0008-transient-epub-opening-parser.md) | accepted | Parse EPUB openings in the transient child |
| [0009](0009-durable-epub-opening-evidence.md) | superseded by 0012; schema rules by 0013 | Bind EPUB opening evidence to inventory snapshots |
| [0010](0010-0-4-neutral-evidence-baseline.md) | accepted | Set the clean-state neutral evidence baseline |
| [0011](0011-isolated-epub-inspector.md) | accepted | Keep EPUB inspection private to an isolated child |
| [0012](0012-metadata-report-activation.md) | accepted; schema policy superseded by 0013 | Activate the two-table metadata-report runtime |
| [0013](0013-pre-release-clean-schema.md) | accepted | Require clean schemas until the first production release |
| [0014](0014-review-case-admission.md) | accepted | Admit immutable review cases atomically |
| [0015](0015-isolated-shadow-reviewer.md) | superseded by 0016 | Isolate one qualification-only shadow reviewer |
| [0016](0016-freeze-explainable-shadow-reviewer.md) | partly superseded by 0019 and 0026 | Freeze explainable publication-date shadow review |
| [0017](0017-bounded-failure-diagnostics.md) | accepted | Bound provider and reviewer failure diagnostics |
| [0018](0018-closed-title-creator-comparison.md) | accepted | Close title and creator comparison |
| [0019](0019-publication-date-precision-comparison.md) | accepted | Compare publication dates by precision |
| [0020](0020-field-specific-acceptable-outcomes.md) | accepted; language rule superseded by 0065 | Model acceptable outcomes per field |
| [0021](0021-coherent-bibliographic-combinations.md) | accepted; amended by 0075 | Bind dependent advice to coherent combinations |
| [0022](0022-decision-ready-reviewer-packets.md) | accepted; amended by 0075 | Give reviewers decision summaries, not decisions |
| [0024](0024-contain-qualification-reviewer-provider-state.md) | accepted | Separate reviewer credentials from disposable provider state |
| [0025](0025-compile-policy-admissible-review-actions.md) | accepted; output mechanism superseded by 0027 | Compile policy-admissible reviewer actions before dispatch |
| [0026](0026-bind-shadow-recommendation-semantics.md) | accepted; amended by 0027 | Bind candidate, keep-current and manual reviewer semantics |
| [0027](0027-provider-admissible-shadow-output-schema.md) | accepted; partly superseded by 0028 | Use a provider-admissible reviewer output schema |
| [0028](0028-normalize-transient-reviewer-json-and-partition-combinations.md) | accepted | Normalise reviewer JSON and partition actions by combination |
| [0029](0029-provider-independent-shadow-decision-preview.md) | accepted | Preview decisions deterministically without a provider |
| [0030](0030-authenticated-review-web-boundary.md) | accepted | Use one authenticated review web boundary |
| [0031](0031-persisted-review-preparation.md) | accepted; partly superseded by 0032 | Persist progressive review preparation |
| [0032](0032-continuous-review-reconciliation.md) | accepted | Reconcile reviews continuously |
| [0033](0033-versioned-policy-draft-preview.md) | accepted | Persist immutable policy drafts before activation |
| [0034](0034-atomic-policy-generation-cutover.md) | accepted | Cut over metadata policy in one atomic generation |
| [0035](0035-persist-non-authoritative-policy-preview-outcomes.md) | accepted | Persist non-authoritative policy Preview outcomes |
| [0036](0036-qualified-decision-only-automatic-policy.md) | accepted | Qualify decision-only automatic policy defaults |
| [0037](0037-origin-neutral-guarded-field-effects.md) | accepted | Use one guarded field-effect boundary for every origin |
| [0038](0038-bounded-single-container-operations.md) | accepted | Bound operational evidence inside the container |
| [0039](0039-continuous-guarded-application.md) | accepted | Apply guarded changes continuously without target CAS |
| [0040](0040-source-only-contributor-workbench.md) | accepted; partly superseded by 0050 | Separate the runtime launcher from the contributor workbench |
| [0041](0041-contract-contained-reviewer-kernel.md) | accepted; partly superseded by 0045 and 0050 | Contract the contained-reviewer kernel |
| [0042](0042-compose-authenticated-browser-operations.md) | accepted | Compose authenticated browser operations before transport |
| [0043](0043-reproducible-react-browser-platform.md) | accepted | Build a reproducible React browser platform |
| [0044](0044-activate-persistent-react-workspace.md) | accepted | Activate one persistent authenticated React workspace |
| [0045](0045-bind-report-cover-packets.md) | accepted | Bind report cover packets to isolated reviewer images |
| [0046](0046-observe-report-bound-covers.md) | accepted | Observe covers with a small closed vocabulary |
| [0047](0047-adjudicate-closed-cover-policy.md) | accepted; partly superseded by 0049 | Adjudicate cover policy deterministically |
| [0048](0048-compile-rich-metadata-policy-preview.md) | accepted | Compile rich-metadata policy previews |
| [0049](0049-owner-confirmed-cover-considerations.md) | accepted; partly superseded by 0059 | Activate owner-confirmed cover considerations |
| [0050](0050-bound-installed-owner-assistance.md) | accepted | Bound installed owner-assistance commands |
| [0051](0051-atomic-current-review-packages.md) | accepted | Publish review packages after projection |
| [0052](0052-record-automatic-no-change.md) | accepted | Record automatic no-change outcomes |
| [0053](0053-dormant-provider-recovery.md) | accepted | Gate dormant provider recovery on one scoped canary |
| [0054](0054-scoped-provider-successors.md) | accepted | Bind retried provider lanes to scoped successor evidence |
| [0055](0055-connection-scoped-preparation-recovery.md) | accepted | Pause review preparation per target connection |
| [0056](0056-edition-overflow-work-witness.md) | accepted | Keep a work-only witness when editions overflow |
| [0057](0057-sustain-explicit-sqlite-aggregate-topology.md) | accepted | Keep the explicit SQLite aggregate topology |
| [0058](0058-settings-authority-and-first-boot.md) | accepted | Make the settings store authoritative after first boot |
| [0059](0059-deterministic-cover-election.md) | accepted | Elect covers deterministically, apart from reviewer advice |
| [0060](0060-dedicated-review-session-database.md) | accepted | Keep review sessions in a dedicated database |
| [0061](0061-audiobook-provider-posture.md) | accepted | Admit keyless audiobook providers, Audible catalog first |
| [0062](0062-owner-managed-reviewer-access.md) | accepted | Keep reviewer access owner-managed and task-scoped |
| [0063](0063-isolated-audiobook-inspector.md) | accepted; multi-file exclusion superseded by 0066 | Read audiobook evidence through two isolated children |
| [0064](0064-audiobook-provider-evidence-vocabularies.md) | accepted | Admit audiobook evidence vocabularies and corroboration rules |
| [0065](0065-language-equivalence-classes.md) | accepted; amended for display names and automatic respelling | Group language codes by base and script |
| [0066](0066-multi-file-audiobook-composition.md) | accepted | Compose multi-file audiobooks into one evidence set |
| [0067](0067-decoration-equivalence-and-compound-admission.md) | accepted; amended for bracketed identifiers | Elect decoration-equivalent titles; admit one author-prefix compound |
| [0068](0068-latin-combining-mark-work-identity.md) | accepted | Admit Latin combining-mark work identity |
| [0069](0069-audiobook-review-cases-and-terminal-outcomes.md) | accepted | Form audiobook review cases and caseless terminals |
| [0070](0070-isbn-backed-series-identity.md) | accepted | Format source-attested ISBN identity only for automatic series fill |
| [0071](0071-book-media-guarded-application.md) | accepted | Extend guarded application to book-media fields and narrators |
| [0072](0072-explicit-local-transport.md) | accepted | Admit explicit local HTTP and browser HTTPS |
| [0073](0073-audiobook-decision-qualification.md) | accepted; partly superseded by 0087 | Qualify each audiobook decision tranche separately |
| [0074](0074-headers-only-media-health-facts.md) | accepted | State media health from header facts |
| [0075](0075-independent-evidence-led-titles.md) | accepted | Elect ISBN-linked ebook titles independently |
| [0076](0076-dependency-build-provenance.md) | accepted; partly superseded by 0087 | Separate dependency build provenance from qualification |
| [0077](0077-owned-responsive-review-reads.md) | accepted | Own responsive review reads with compact currentness checks |
| [0078](0078-preparation-claim-and-crash-recovery.md) | accepted | Keep preparation ownership and recover from crashes |
| [0079](0079-supported-title-preservation-and-recovery.md) | accepted | Preserve supported titles and requeue explicit recovery |
| [0080](0080-isbn-evidence-without-image.md) | accepted | Verify ebook ISBNs without image retrieval |
| [0081](0081-image-owned-reviewer-provisioning.md) | accepted | Ship and validate the contained reviewer profile |
| [0082](0082-runtime-activity-facts.md) | accepted | Reuse Activity facts with exact source witnesses |
| [0083](0083-validated-browse-document-reuse.md) | accepted | Reuse validated browse documents by exact bytes |
| [0084](0084-persisted-attention-facts.md) | accepted | Persist which fields need the owner |
| [0085](0085-owner-entered-values-withdrawal-and-reselection.md) | accepted | Admit owner-entered values, withdrawal and reselection |
| [0086](0086-deployment-held-setup-claim-token.md) | accepted | Require a deployment-held claim token for the first owner |
| [0087](0087-operation-bound-qualification-authority.md) | accepted | Bind qualification authority to permitted operations |
| [0088](0088-carried-recording-admission.md) | accepted | Carry an admitted audiobook edition into the next preparation |
| [0089](0089-unmatched-audiobook-research.md) | accepted | Search an unmatched audiobook again |
| [0090](0090-hardcover-audio-edition-witness.md) | accepted | Hardcover audio editions witness which recording an audiobook is |
