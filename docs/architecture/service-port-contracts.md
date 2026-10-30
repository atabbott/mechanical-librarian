# Application service contracts

Each application operation declares the narrow contracts it needs as Python
protocols beside it in `src/mechanical_librarian/application/`. Contracts
exchange typed, bounded application or domain values. Vendor payloads,
transport clients, SQLite rows, credential bytes and filesystem operations
stay inside adapters, and only the composition root chooses an adapter
([ADR-0006](decisions/0006-port-adapters-and-composition-root.md)). There is
no shared ports module or generic provider, reviewer or job framework.

## Contract families

- **Catalogue.** `CatalogueReader` and `CatalogueRepository` observe and
  publish one bounded current generation.
- **Evidence.** `TargetReader`, `EpubInspector`, `AudioInspector`,
  `MetadataProvider`, `CoverObserver` and `ReportRepository` read one current
  item, inspect its media in an isolated child, observe providers and covers,
  recheck currentness and publish one immutable report with its artifacts.
- **Proposals and review.** `ProposalSetRepository`, `ReviewCaseRepository`
  and `ReviewPipelineRepository` reopen evidence by exact ID, store
  ProposalSets, admit cases at the exact-current boundary and persist
  preparation progress.
- **Policy and decisions.** `PolicyPreviewRepository`,
  `CurrentPolicyResolutionRepository`, `AutomaticSafetyRepository` and
  `DecisionPreviewRepository` resolve, preview and record policy outcomes
  without model or provider calls.
- **Guarded effects.** `FieldEffectRepository` and
  `ContinuousFieldEffectRepository` own plans and their state. `TargetService`
  is the only write port; `BackupReadiness`, `CapabilityObserver`,
  `AuthorityCurrentness`, `ConsentCurrentness`, `DependencyCurrentness`,
  `CoverRecovery` and `CoverArtifactReader` supply the guards each claim
  rechecks.
- **Reviewers.** `ContainedReviewer`, `OpenRouterClient`, `CodexAuth`,
  `ReviewerAccessRepository`, `CoverConsiderationStore` and
  `DescriptionAdviceRepository`; see [below](#reviewers).
- **Provider recovery.** `ProviderRecoveryStore` and its observers lease and
  retry failed provider lanes
  ([provider recovery](../operations/provider-recovery.md)).
- **Settings and setup.** `OwnerSettingsRepository`, `SetupStateRepository`,
  `SetupJourneyRepository`, `SetupConnectionProbe` and
  `SetupLibraryDiscovery`.
- **Diagnostics.** `TimingRepository` and `SupportExporter`.

## Browser operations

The web layer receives one pre-composed `ReviewApplication`, never a
repository. Its named operations run behind one admission gate, except
Library and Attention, which read through their own borrowed read connection.
The SQLite adapter alone owns transactions
([ADR-0042](decisions/0042-compose-authenticated-browser-operations.md),
[ADR-0077](decisions/0077-owned-responsive-review-reads.md),
[overview](overview.md#serialisation-and-storage)).

## Provider results

All metadata adapters return the application-owned, immutable
`ProviderResult`: `observation`, `cover_descriptors`, `evidence` and
`work_ladders`.
`ProviderOperationalEvidence` carries the first `accessed_at`, bounded
`attempt_records` and `incomplete_causes`; `None` means the evidence is
unavailable and `()` means it is known to be empty. Callers use the returned
snapshot, never adapter attributes.

A declared failure carries a `ProviderFailureSnapshot` with the same
descriptors and evidence. `ProviderObservationFailure`,
`ProviderObservationCancelled` and `ProviderObservationInterrupted` chain the
original cause, and the last two still propagate as
`asyncio.CancelledError` and `KeyboardInterrupt`. Client cleanup never
replaces an active failure. Retry inside a request stays private to the
adapter, within the shared deadline, byte limit and pacing. A cover descriptor
keeps only its acquisition-time pacing callback, owned elsewhere; snapshots
never copy clients, pacers or locks.

## Reviewers

The contained Codex reviewer and the OpenRouter client serve two installed
callers: cover considerations and description advice. Each request is one
sanitised canonical JSON input, a pinned instruction and a closed output
schema. The result is one locally validated JSON object or a typed, value-free
failure. The cover caller adds only report-bound PNG previews. Callers persist
only locally admitted output.

Reviewer adapters receive no target or provider credentials and have no
decision or write authority. A cover retry is an explicit owner action,
refused once the owner has decided the cover; it creates a superseding
consideration. The boundary is in [AI trust boundary](ai-boundary.md).
