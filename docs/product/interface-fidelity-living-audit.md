# Interface fidelity living audit

Status: protected Library workflow candidate under local qualification; delivery requires exact-main readback
Last updated: 2026-08-13
Current implementation baseline: `29cac093fc628e89e5aa49916abc3bc7e32edd8a`
Protected review reference: owner-selected Library model at
`377b8456d6f0070d703b7ed3fd372c5c0ac81366`
Impeccable critique baseline: 28/40 on 2026-08-11

## Purpose

This is the working defect-and-remediation record for the authenticated owner
interface. It exists because passing route, responsive, and synthetic browser
checks does not prove that the installed application preserves Variant M's
desktop operating model, information hierarchy, or usefulness with
representative evidence.

The document is deliberately a living audit rather than a completion report.
A row moves to **verified** only when the exact installed application has been
directly exercised with representative data and compared with the governing
reference. A green unit test or an assertion that the page has no horizontal
overflow is supporting evidence, not a substitute for that comparison.

## Governing outcome

At regular desktop widths the normal review experience is one coherent work
surface:

- the complete Library remains visible and searchable;
- attention is an in-context queue or drawer, not a competing top-level
  destination;
- the selected work retains readable identity and status context;
- an attention-only field filmstrip makes the remaining work and position
  obvious;
- the current value and every eligible proposed value are the direct decision
  controls;
- reviewer facts, evidence provenance, and bounded apply state support the
  decision without displacing it; and
- recording one value advances through unresolved fields and works without a
  round trip through another top-level page.

At narrower regular widths, the Library index and selected detail remain
concurrent while attention becomes a contextual drawer. At phone widths,
Library, queue, and selected detail may become separate single-column steps,
but they retain the same field position, current-value filmstrip, automatic and
recorded context, source ledger, apply state, and cross-work movement. Responsive
adaptation may change spatial composition; it must not discard useful Variant M
information merely because the viewport is narrower.

The prototype is directional rather than a source-code or pixel-copy contract,
but its observable composition, hierarchy, density, and interaction language
are protected. A materially different replacement requires a concrete product
or technical constraint and evidence that the replacement better preserves
the outcome.

## Reopened semantic-completeness correction

Issue #598 extends the protected field strip: automatic, unsupported, and
recorded fields stay directly inspectable in their stable primary positions.
Only server-classified owner attention advances normally. Automatic abstention
is an evidence-backed manual fallback; unavailable empty values expose an
explanation rather than a false choice.

The first #554 merge established the correct route and workspace architecture,
but a direct semantic comparison found that the selected-work surface still
compressed several protected Variant M signals. The reopened correction is one
coherent #554 batch; none of these rows may be closed by substituting a compact
layout or by pointing to existing safety behavior.

| ID | Status | Required correction | Preserved strength |
| --- | --- | --- | --- |
| SC-01 | implemented; affected proof passed | Automatic, recorded, and no-eligible-change siblings are inspectable beside the attention-only strip, with their retained value and reason. | They remain subordinate to actionable fields and never expose false decisions. |
| SC-02 | implemented; affected proof passed | Every attention-strip item shows field name, stable position, state, and current value. | URL-owned field selection, focus, and authoritative advancement remain intact. |
| SC-03 | implemented; affected proof passed | The embedded source ledger separates source, observed value, and basis and demonstrates local, Project Gutenberg, Google Books, Open Library, and hostile provider-shaped evidence without treating record count as provider authority. | Full progressive evidence, pagination, failure recovery, bounded strings, and hostile-input safety remain available. |
| SC-04 | implemented; affected proof passed | The selected field exposes concise external-library capability/apply context and says explicitly that deciding queues nothing. | Backup, consent, currentness, validation, application, and read-back remain separate gates. |
| SC-05 | implemented; affected proof passed | Attention rows include ordinal, work identity, authors, library/media, field reason, and complete work count; explicit Previous/Next actions support sustained cross-work review. | Search, lenses, cursor pagination, Back/Forward, deep links, and deliberate navigation during mutation remain supported. |
| SC-06 | implemented; affected proof passed | Embedded decision failure offers an explicit same-decision retry using the exact preserved command and idempotency key. | There is still no automatic mutation retry; stale, ambiguous, already-decided, and reconciliation-lock behavior remains fail-closed. |
| SC-07 | implemented; affected proof passed | M's information hierarchy transfers responsively: wide three-column context, narrow concurrent master/detail plus queue drawer, and a phone task flow containing the same semantic context. Narrow shell labels do not wrap and the evidence ledger becomes labeled cards before table columns become cramped. | Existing 44px targets, focus, dark/light themes, long-value containment, and zero document overflow remain protected. |

## Current defect register

The `verified on #554 merge` labels below record the first architectural pass.
They are not current closure claims while SC-01 through SC-07 remain pending;
the reopened correction table above is authoritative for milestone completion.

| ID | Severity | Status | Defect | Required fix | Closure proof |
| --- | --- | --- | --- | --- | --- |
| IF-01 | P0 | verified on #554 merge | **Library and Attention are separate permanent navigation destinations.** Variant M keeps Library as the normal workspace and opens its field-level attention queue in context. The current split changes the mental model and forces avoidable page changes. | Remove Attention as a peer desktop destination. Provide one Library workspace with an attention count/control and an independently openable, URL-addressable queue/drawer. Preserve the current separated compact flow below the agreed breakpoint. | Wide installed app shows Library, queue, and selected work in one document; queue open/close, deep links, refresh, and Back/Forward are deterministic; 390px retains a coherent separate-surface flow. |
| IF-02 | P0 | verified on #554 merge | **The desktop workspace is materially less informative and less useful than Variant M.** Large widths are spent on sparse cards and route-level framing instead of concurrent work identity, field position, decision context, and evidence. | Restore an intentional wide composition: bounded searchable work index, selected-work context, attention-only field filmstrip, primary decision canvas, and supporting context. Use the available width for comparison and navigation rather than enlarging empty space. | Direct comparison at 1440px and a narrower desktop width demonstrates equivalent information hierarchy and task throughput with long, hostile, empty, loading, failure, stale, and resolved states. |
| IF-03 | P0 | verified on #554 merge | **Moving between works that need attention is too costly.** The owner can fall out of the review flow and has to return to Library or Attention to select the next work. | Keep the unresolved queue and work position available while reviewing. After a decision, advance through the frozen visible unresolved order; also provide explicit previous/next work or queue selection without losing list position or filters. | Keyboard-only and pointer journeys process fields across at least four works without revisiting a top-level Library/Attention destination; deliberate navigation during a pending mutation is preserved. |
| IF-04 | P1 | verified on #554 merge | **The field navigator is weaker than Variant M's attention-only filmstrip.** The current detail route gives insufficient at-a-glance information about other unresolved fields, automatic/resolved siblings, and the owner's place in the queue. | Add the protected attention-only field strip on wide layouts, with field name, state, current selection, and unambiguous active position. Keep automatic/resolved siblings inspectable through a subordinate disclosure rather than mixing them into the action queue. | A work with several actionable, current-only, automatic, and resolved fields remains scannable; strip position is stable after a decision and is keyboard operable. |
| IF-05 | P1 | verified on #554 merge | **Direct-value choices are semantically correct but visually and informationally underpowered.** The cards do not yet provide the prototype's strong comparison hierarchy or enough bounded reason/provenance context at desktop width. | Preserve value-as-action with no generic Accept/Keep/Reject or submit step. Strengthen current/proposed distinction, value typography, supporting reason, corroboration/conflict summary, and source diversity without turning evidence into an authority claim. | Current plus several scalar and structured proposals can be compared without opening another page; long values wrap; recorded, automatic, stale, no-proposal, and ambiguous states never expose false controls. |
| IF-06 | P1 | verified on #554 merge | **Selected-work context is too thin.** The current composition does not match Variant M's richer bibliographic identity, work-level attention status, field progress, automatic-resolution disclosure, and compact apply context. | Restore a concise work header and work-level summary adjacent to the decision. Show readable title, authors, library/media destination, actionable-field progress, and calm automatic/apply status. Keep opaque identifiers and internal codes subordinate or absent. | Representative ebook and audiobook works with long/multiple authors, several libraries, mixed field states, and hostile content remain readable in light and dark themes. |
| IF-07 | P0 | verified on current `main` | **Activity does not load in the locally hosted installed application.** This prevents direct evaluation of the protected Activity surface and invalidates any claim that all primary routes are inspectable. | Reproduce against the exact installed head, capture the failing request and rendered error, and fix the smallest owning seam. Do not mask a production transport/mapping defect with fixture substitution. | Native navigation and direct URL load Activity after authenticated sign-in with zero unexpected console/page/network errors; loading, empty, partial consent, history, recovery, and failure states remain reachable. |
| IF-08 | P1 | verified on #554 merge | **Evidence data does not demonstrate multiple providers.** The visible cohort appears dominated by Project Gutenberg, so provider diversity, corroboration, contradiction, missing-provider, and mixed-provenance layouts have not been meaningfully inspected. | Extend the safe fixture with several provider identities and roles, including local evidence, Project Gutenberg, Google Books, Open Library, and an unavailable/unknown provider state. Include corroborating and conflicting values, repeated records from one provider, long provider labels, and evidence distributed over multiple pages. Provider count must not be inferred from evidence-record count. | Installed detail shows at least three providers on one work and different provider combinations across works; source labels remain readable and neutral; conflicts, duplicates, missing data, pagination, and hostile provider text are exercised at desktop and 390px. |
| IF-09 | P2 | verified on current `main` | **Policy is closest to the intended visual world, but its notebook can become cognitively dense at scale.** Simultaneous field choices, sticky impact, chapter navigation, safety disclosure, and activation review can make policy authoring feel more intimidating than necessary. | Preserve Refined Policy B's semantic notebook and guarded activation model. Make changed and unsafe fields primary, let unchanged chapter content collapse to a scannable summary, and clarify the sequential preview → impact → activation cadence. | Desktop and 390px light/dark direct inspection, keyboard/modal focus proof, hostile/long content, and all validation/preview/activation/currentness/failure states pass with no unresolved material critique finding. |
| IF-10 | P2 | verified on #554 merge | **Library Preview and title-link actions have weakly differentiated consequences.** One opens a shallow selected-work preview while the other enters the review route, but their labels and visual hierarchy do not make that difference sufficiently predictable. | Either unify both entry points around one selected-work action or give each an explicit purpose-based label and supporting state. Do not make the title behave like an ambiguous hidden shortcut. | First-time and keyboard-only users can predict each action before invoking it; Back/Forward restores the same Library selection, filters, and scroll position. |
| IF-11 | P3 | verified on current `main` | **The circular `ML` brand mark discards Prototype M's distinctive asymmetric book-spine identity.** This does not block review, but it weakens the authored catalogue character while retaining a more generic application badge. | Restore or reinterpret the asymmetric book-spine mark in the production shell, with sufficient contrast and no fabricated cover-art implication. | The mark remains legible in light/dark and compact/desktop shells, has an appropriate accessible treatment, and does not displace primary navigation. |

## Impeccable critique baseline

The first independent dual-assessment critique of `frontend/src` scored the
Operate experience **28/40**. The result is a baseline for comparison, not a
numeric release gate. The archived maintainer snapshot is
`.impeccable/critique/2026-08-11T06-31-42Z__frontend-src.md`.

| # | Nielsen heuristic | Score | Owning finding |
| --- | --- | --- | --- |
| 1 | Visibility of system status | 3/4 | Guarded states are strong; cross-work and remaining-field position are not persistent (IF-03, IF-04). |
| 2 | Match between system and real world | 3/4 | Bibliographic language fits; Attention as a top-level place weakens the workbench model (IF-01). |
| 3 | User control and freedom | 2/4 | Leaving review loses contextual queue and position (IF-03). |
| 4 | Consistency and standards | 3/4 | Visual patterns are coherent; the information architecture contradicts Variant M (IF-01, IF-02). |
| 5 | Error prevention | 4/4 | Exact-current decisions, reconciliation lockout, and guarded commands are exemplary and must not regress. |
| 6 | Recognition rather than recall | 2/4 | Separate routes require the owner to remember work, field, evidence, and queue state (IF-01–IF-04). |
| 7 | Flexibility and efficiency | 2/4 | Search and lenses exist, but cross-work review pays a route-change tax (IF-03). |
| 8 | Aesthetic and minimalist design | 3/4 | The visual system is calm; route framing consumes desktop space needed for comparison (IF-02, IF-05, IF-06). |
| 9 | Error recovery | 4/4 | Stale, ambiguous, failed, and reconciling states are unusually responsible and must not regress. |
| 10 | Help and documentation | 2/4 | Safety copy is strong; provenance, conflict, and policy consequences are fragmented across views (IF-05, IF-08, IF-09). |

The critique's five priority findings map directly to IF-01/IF-02, IF-03,
IF-07, IF-04, and IF-05/IF-08. It also added IF-10 and IF-11 rather than
duplicating them under the broader workspace findings.

The deterministic design scan reported five warnings in
`frontend/src/styles.css`: four semantic three-pixel left rules and the use of
Inter. These are documented design-system choices, not accepted defects. The
left rules mark notices, status, recovery, and current ledger events; Inter is
the operating typeface paired with Georgia. No detector warning is being used
as a substitute for the structural findings above.

## Post-remediation Impeccable critique

The delivered #555 interface scores **37/40**, up from the 28/40 tracked baseline. This is
trend evidence rather than a release threshold. Policy now shows one chapter at
a time with a visible four-stage cadence; Activity leads with current guarded
state, recovery, and outcomes; the full review gives its decision canvas the
dominant desktop width; operational type and touch targets meet the documented
floor; and supported hard entries reach either sign-in or the designed client
recovery. Direct-value decisions deliberately remain immediate because that is
the protected decision model; currentness, ambiguity lockout, and explicit
safe-recovery copy carry its error-prevention burden.

| # | Nielsen heuristic | Score | Post-remediation disposition |
| --- | --- | --- | --- |
| 1 | Visibility of system status | 4/4 | Current Activity state, recovery, outcomes, the Policy draft/preview/activation stage, queue position, and mutation status lead their surfaces. |
| 2 | Match between system and real world | 3/4 | Bibliographic language and purpose labels are strong; a bounded amount of safety vocabulary remains. |
| 3 | User control and freedom | 3/4 | URL-owned context, Back/Forward restoration, retry, pause, revoke, disclosures, and reversible Policy activation review are explicit; protected value choices remain immediate. |
| 4 | Consistency and standards | 4/4 | The shell, workspace, notebook, ledger, focus, and responsive language now form one coherent system. |
| 5 | Error prevention | 4/4 | Currentness, CSRF, guarded commands, ambiguity lockout, and direct-value eligibility remain intact. |
| 6 | Recognition rather than recall | 4/4 | Work, field, queue, choice, source, Policy chapter, field meaning, qualification, representation, and Activity state stay visible at the point of use. |
| 7 | Flexibility and efficiency | 4/4 | Search, lenses, contextual queue, cross-work advance, compact adaptation, and keyboard operation support sustained review. |
| 8 | Aesthetic and minimalist design | 4/4 | Decision-led allocation, one-chapter disclosure, state-first Activity, readable type, and the asymmetric mark restore hierarchy without decoration. |
| 9 | Error recovery | 4/4 | Failed, stale, ambiguous, missing-case, asset, consent, and reconciliation paths expose bounded recovery. |
| 10 | Help and documentation | 3/4 | Consequence and safety guidance are present in context; no separate help destination is needed for this bounded owner workspace. |

### Cognitive-load and emotional findings

- Four permanent destinations and three review subviews divide one continuous
  review task.
- Queue position, selected work, remaining fields, values, and evidence cannot
  remain visible together on desktop.
- Evidence and History disclose at the wrong level by replacing the decision
  rather than supporting it.
- Policy and Activity can legitimately expose more than four choices, but need
  stronger grouping and sequencing as libraries, fields, and proposals scale.
- The opening experience feels private and trustworthy, and direct decisions
  remain reassuring. The emotional valley is the navigation between modules;
  the system's defensive explanation currently peaks more strongly than the
  owner's productive flow.

### Persona findings

- A technically capable self-hoster sees a tool optimized for explaining its
  safety architecture more than clearing exceptions.
- A first-time owner must infer the relationship among Library Preview, title
  links, Attention, Review, Evidence, History, Policy, and Activity before a
  stable mental model emerges.
- A power reviewer benefits from fast direct-value commands but pays a recall
  and navigation tax after every cross-work decision.

## Delivery tracking

Milestone [#19, 0.14.1 — Interface fidelity restoration](https://github.com/atabbott/mechanical-librarian/milestone/19)
was reopened on 2026-08-11 after the installed-interface review and Impeccable
critique disproved the earlier completion claim. Corrective delivery is:

1. [#554 — Match the Library and Attention workspace to protected Variant M](https://github.com/atabbott/mechanical-librarian/issues/554)
   merged as `9ba487b9129e5908a29094f6e95179040e481013` after its final reviewed
   head `067a1125b9626753d6f850564cdf19fb095779fc` passed 74 frontend tests,
   hosted gates, and connector C1 remediation of all eight accepted findings.
   It owns IF-01 through IF-06, IF-08, and IF-10. That merge established the
   contextual workspace architecture, but #554 was reopened after semantic
   comparison found the SC-01 through SC-07 gaps above. The current correction
   must complete M's information model across wide, narrow, and phone layouts
   without regressing the existing product safeguards.
2. [#555 — Polish the authenticated frontend after fidelity restoration](https://github.com/atabbott/mechanical-librarian/issues/555)
   owns IF-07, IF-09, IF-11, the remaining accepted cross-surface critique
   findings, the post-remediation Impeccable critique, and final milestone
   reconciliation. Its reviewed head
   `c3f30a020fa1ae9e2c4979b9a6dcb7eb9c9b0e34` merged as
   `781d5658c8e9d66695a743e1be4460dd4216cb53` after both bounded audit rounds,
   connector C1 remediation, hosted checks, and exact-current-main
   qualification passed.

Closed #499 remains the historical tracker for the completed first pass. It is
not evidence that the corrective defects are resolved and is not reopened by
this follow-up ledger.

## Previously corrected findings that must not regress

These six delivered corrections remain part of the audit baseline. They are not
evidence that the open defects above are complete.

| Baseline | Correction to preserve | Existing tracking |
| --- | --- | --- |
| B-01 | Rich authenticated interface fixture and direct-route proof instead of a single narrow case. | [#532](https://github.com/atabbott/mechanical-librarian/issues/532) / [PR #551](https://github.com/atabbott/mechanical-librarian/pull/551) |
| B-02 | Touch-safe Library lenses, title links, Preview controls, pagination, and bounded long-title wrapping. | [#535](https://github.com/atabbott/mechanical-librarian/issues/535) / [PR #549](https://github.com/atabbott/mechanical-librarian/pull/549) |
| B-03 | Direct current-or-specific-value decisions with no generic decision controls or separate submit step. | [#531](https://github.com/atabbott/mechanical-librarian/issues/531) |
| B-04 | Case-wide reconciliation lock after an ambiguous or failed mutation when authoritative refresh is unavailable. | [#531](https://github.com/atabbott/mechanical-librarian/issues/531) / [PR #553](https://github.com/atabbott/mechanical-librarian/pull/553) |
| B-05 | Semantic Policy notebook with guarded validate, preview, review, activation, and currentness states. | [#536](https://github.com/atabbott/mechanical-librarian/issues/536) |
| B-06 | Readable Activity consent, state, history, and grouped recovery ledger with a closed transport projection. | [#533](https://github.com/atabbott/mechanical-librarian/issues/533) / [PR #547](https://github.com/atabbott/mechanical-librarian/pull/547) |

If an open defect is caused by regression inside one of these owning outcomes,
reopen that constituent rather than creating a replacement ticket. Create a
new ticket only when the defect is genuinely outside the earlier acceptance
boundary.

## Target desktop composition

The intended desktop arrangement is a responsive operating workspace, not
three unrelated pages placed beside one another:

```text
Library workspace
├── Work index
│   ├── search, lenses, page context
│   └── complete attention-first work list
├── Selected work
│   ├── readable identity and work-level status
│   ├── attention-only field filmstrip
│   └── current value + specific proposed-value actions
└── Contextual queue/support
    ├── unresolved fields across works
    ├── reviewer facts and progressive evidence
    └── bounded automatic/apply context
```

The queue may be a drawer, rail, or other production-quality composition, but
it must be independently addressable and must not become a fifth permanent
destination. Evidence and automatic history are progressive support; they must
not push the active decision below an avoidable wall of metadata.

## Representative-data matrix

The audit fixture uses real public-domain works and irreversibly sanitized or
synthetic provider-shaped evidence. It must not contain private library data,
credentials, source paths, raw provider responses, or copied production
databases.

| Dimension | Minimum useful cohort |
| --- | --- |
| Works | At least 20 real public-domain works across ebook and audiobook libraries, with enough unresolved work to require queue navigation and pagination. |
| Identity | Short, long, Unicode, hostile-looking, edition-qualified, anthology, multi-author, no-author, series, and same-title/different-author works. |
| Fields | Title, structured authors, series, sequence, publication date, publisher, language, description, and cover state where supported by the current interface. |
| Decisions | Current-only, one proposal, several proposals, structured proposals, automatic, resolved human, no-attention, stale, and indeterminate post-mutation states. |
| Evidence | Local plus at least Project Gutenberg, Google Books, and Open Library; corroboration, contradiction, duplicates, unavailable provider, unknown future provider code, long labels, and multi-page evidence. |
| Runtime | Loading, empty, partial, one-time failure/retry, persistent failure, stale/currentness conflict, ambiguous mutation, and recovery. |
| Presentation | 1440px, narrower desktop, 390px, light, dark, keyboard, visible focus, long content, no document overflow, and no hidden critical controls. |

Provider names in this matrix describe safe fixture provenance identities, not
authorization to call live services during UI tests.

## Delivery plan

1. **Restore inspectability.** Reproduce and fix IF-07 so every primary route
   can be audited in the installed application.
2. **Freeze the wide workspace contract.** Capture annotated Variant M and
   current-application screenshots at the same desktop widths. Record which
   existing URLs, API projections, currentness rules, and query state can be
   composed without widening authority.
3. **Deliver one desktop review workspace.** Address IF-01 through IF-06 as a
   coherent interaction outcome. Do not split the composition into cosmetic
   tickets that individually leave the workflow weaker.
4. **Qualify representative provenance.** Address IF-08 in the durable fixture
   and use it to inspect the new workspace, not merely to add assertions.
5. **Clarify Library entry actions.** Address IF-10 within the unified desktop
   workspace rather than preserving two ambiguous routes into the old model.
6. **Refine Policy.** Address IF-09 only after the primary review workflow is
   stable, so Policy polish does not conceal a broken core task. IF-11 may be
   included only when the owning shell is already changing.
7. **Reconcile tracking and documentation.** Reopen predecessor issues where
   their protected acceptance was not met, link exact fixes and proof here,
   and remove any completion claim contradicted by the installed application.

## Verification record

Add dated entries; do not overwrite earlier observations.

| Date | Exact head | Surface/data | Width/theme/input | Result | Evidence or follow-up |
| --- | --- | --- | --- | --- | --- |
| 2026-08-14 | F24 activation candidate | Authenticated Activity provider recovery | protected 1440px, 1024px, and 390px fixture contract; light/dark; keyboard and pointer | source contract and exact browser qualification pass | All six durable phases are explicit: safe waiting, health check, work-counted fanout, recovered, dormant configuration/authentication, and exhausted/nonretryable. Copy exposes no identifiers, queries, headers, credentials, payloads, or paths; there is no provider-retry control. The existing #591 heading focus, filters, date range, page size, pagination, evidence disclosure, target sizing, reduced motion, and overflow contracts remain unchanged. |
| 2026-08-11 | candidate tree from `29cac09` | Reopened #554 semantic-completeness audit | 1440px, 884px, and 390px; light/dark; rich 20-work cohort | affected source/browser proof passed; exact delivery receipts are recorded on the pull request | Installed comparison confirms concurrent wide and narrow master/detail, phone task continuity, attention position and movement, automatic/recorded context, five named sources with separate observations/basis, apply context, inert hostile values, and zero horizontal overflow. Fresh 884px entry selects a reviewable work and retains the concurrent workspace. The single Impeccable audit batch found narrow masthead wrapping and cramped ledger columns; both were remediated at their CSS seam and added to the browser contract. |
| 2026-08-11 | `7fd8f8a` | Current installed Library/Attention/detail versus Variant M | regular desktop | fail | IF-01 through IF-06 confirmed by product review; current composition does not preserve the protected desktop operating model. |
| 2026-08-11 | `7fd8f8a` | Current installed evidence cohort | local fixture | incomplete | IF-08: visible provenance does not demonstrate meaningful multi-provider behavior. |
| 2026-08-11 | `7fd8f8a` | Current installed Activity | local fixture | fail | IF-07 reported: route cannot be loaded; exact request/console reproduction still required. |
| 2026-08-11 | `7fd8f8a` | Full frontend Impeccable critique | source, protected references, and bounded browser evidence | 28/40 | Dual independent assessments confirmed IF-01–IF-05 and IF-07/IF-08, advanced IF-09 to refinement, and added IF-10/IF-11. Browser overlay injection failed, so no overlay evidence is claimed. |
| 2026-08-11 | `7fd8f8a` | Current installed Policy | desktop and compact | provisional pass | Semantics broadly accepted; critique found bounded density and activation-cadence refinement under IF-09. |
| 2026-08-11 | `067a112` reviewed head; `9ba487b` merge | Variant M Library workspace installed build | 390px → 1440px, rich safe fixture | pass | Frontend 74/74, typecheck, lint, production build, hosted gates, artifact browser gate, and diff check passed. Direct-value canvas, filmstrip, URL-owned attention rail, shared authoritative settlement, and compact single-task priority match the protected operating composition. The safe fixture contains 20 public-domain works and Local, Project Gutenberg, Google Books, and Open Library provider-shaped evidence. |
| 2026-08-11 | `067a112` reviewed head; `9ba487b` merge | Connector C1 and aggregate browser qualification | PR #557 | eight P2 findings accepted and remediated | Remediation blocks superseded controls, settles durable status after route departure, restores selected-field focus and continuity, clears stale fields on work switches, paginates and recovers the attention rail, hardens fallback idempotency keys, and updates aggregate browser proof to the contextual workspace. |
| 2026-08-11 | `ab79291` initial head; `c3f30a0` reviewed head | Audit round 1: Policy, Activity, full review, shell and compact adaptations | 1440px, 1024px, and 390px; light/dark; keyboard and pointer | pass after one remediation batch | The full-review rail is 240px and its decision article 1093px at 1440px, with two-up direct choices and one-column compact choices. Policy collapsed chapters no longer squeeze into pathological narrow columns. Activity shows current state and recovery from 230px/369px at desktop before a reachable consent disclosure. Compact reviewer facts no longer consume 320px. Rich 20-work data, multiple providers, hostile text, loading, empty, failure, stale, and ambiguous states remain reachable. |
| 2026-08-11 | `ab79291` initial head; `c3f30a0` reviewed head | Audit round 2: installed hard entry and refresh | authenticated `/review/missing-case`, root entry, and genuine missing-case API | pass after final remediation batch | The HTML shell no longer treats an opaque client path segment as case authority; root redirects to `/login`; repeated hard review documents return the exact built shell; the API retains canonical `not_found`; the client renders its bounded recovery. Frontend 74/74, the activation module, and all four exact-built browser tests pass. No third audit round was opened. |
| 2026-08-11 | `c3f30a0` reviewed head; `781d565` merge | Connector C1 delta qualification | Policy cadence, compact review rail, Activity consent summary | three P2 findings accepted and remediated | Explicit Policy progress distinguishes validation, saved draft, current preview, inspected impact, and successful activation, and resets on edit. The exact compact rail selector computes static/auto while desktop remains sticky/82px. Activity readiness and its enabled controls share backup, capability, unconsented-destination, and prepared-work gates. Frontend 76/76 and all four exact-built browser tests pass. |
| 2026-08-11 | `781d565` exact current `main` | Milestone exit qualification and installed readback | complete source/frontend/browser/installed/container gates; 1440px and 390px, light/dark | pass | Generated artifacts reproduced byte-for-byte; frontend 76/76, fast 1,159 with nine documented skips, rich browser 4/4, exact installed complete including slow/resource, and container lifecycle v3 passed. Direct installed comparison covered every protected surface with the 20-work multi-provider cohort, zero horizontal overflow, inert hostile content, direct-value controls only, reachable progressive evidence, and no material console, functional, security, visual, documentation, CI, or review finding. |
| 2026-08-11 | PR #561 reviewed head | Connector C1 semantic and responsive delta | stable field positions, 701–760px concurrency, intermediate drawer clearance, evidence failure | accepted and remediated | The actionable filmstrip freezes the case's pre-decision order, 701–760px retains both work index and selected detail, the attention drawer clears a wrapped masthead, and supplemental-evidence failure leaves retained provenance visible. Focused unit and exact-browser regressions cover the four findings; final exact-head and installed qualification bind the merge decision. |

## Definition of done

This audit may be marked complete only when:

- every P0 and P1 row is verified on exact current `main`;
- no desktop claim relies on the compact layout as justification;
- all five primary surfaces load in the real installed application;
- the review workflow is directly compared with Variant M at matched widths;
- the representative-data matrix is exercised, including multi-provider
  evidence and cross-work queue navigation;
- accepted Impeccable, accessibility, functional, security, and review
  findings are resolved with exact-head proof; and
- a post-remediation independent critique is recorded against the exact
  installed head, improves on the 28/40 baseline, and leaves no material P0 or
  P1 critique finding; and
- GitHub issues, project state, documentation, and completion claims agree
  with the installed result.

The first-pass conditions were recorded on `781d565`, but the later semantic
comparison invalidated that completion claim for #554. This dossier returns to
complete only after SC-01 through SC-07 are qualified on the exact reviewed
head, merged, and read back from current `main` with tracking reconciled.
