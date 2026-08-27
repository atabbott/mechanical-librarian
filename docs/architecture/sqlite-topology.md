# SQLite aggregate topology

This is the responsibility map for the exact-current application database. It
is an architectural inventory, not a second schema definition: the executable
definition and open-time exact-object validation remain in
[`schema.py`](../../src/mechanical_librarian/adapters/persistence/schema.py).

#912 (ADR-0059) later moved the two security/session tables —
`review_sessions` and `review_login_failures` — out of the application
database into the dedicated session database
`private/review-sessions.sqlite`, so owner sign-in never queues behind a
scan's write transaction. Rows and counts below that name those two tables
are retained August 2026 audit evidence of the pre-#912 schema; the current
application database no longer contains them.

## Audit boundary and evidence

The map was checked against the exact-current schema, production persistence
callers, and a consistent SQLite backup of the retained-library qualification
database. The backup was created through SQLite's backup API from a read-only
volume mount, interrogated only with aggregate `count(*)` and `dbstat` queries,
then moved to the operator's trash. No value, identifier, path, document,
artifact, or database page is retained here.

That August 2026 snapshot contained 42 application tables, 7 named indexes,
30 triggers, and 398,168,064 bytes. Counts and KiB below are aggregate evidence
only; they describe retained extent, not a public fixture or a capacity promise.

The production code has no durable per-table write telemetry. The write evidence
below therefore uses final row-cardinality deltas from a public-safe bounded
workload; it does not infer frequency from retained rows or claim a production
rate.

`R` means rows in that snapshot and `KiB` means `dbstat` pages rounded to the
nearest KiB.

## Tables and production ownership

| Responsibility | Exact table | Retained extent | Producer and reader ownership | Why it remains separate |
| --- | --- | --- | --- | --- |
| Canonical immutable authority | `metadata_reports` | 2,064 R / 115,704 KiB | `reports.publish`; report, proposal, case, package, cover and activity readers | One exact report is the evidence authority; its content/file identity is not a current pointer. |
| Canonical immutable authority | `metadata_report_cover_references` | 13,446 R / 3,196 KiB | `reports.publish`; cover artifact/packet readers | Per-report source-to-artifact binding is immutable and allows artifact checks without reparsing the report document. |
| Canonical immutable authority | `proposal_sets` | 2,060 R / 22,472 KiB | `proposal_sets.publish_proposal`; admission and policy readers | Evaluated proposal is a separately validated, immutable consequence of one report and policy. |
| Canonical immutable authority | `review_cases` | 2,028 R / 47,120 KiB | `review_cases.admit_review_case`; browse, decision, cover and activity readers | Owner-actionable immutable packet; it cannot be merged into its changing lineage pointer. |
| Canonical immutable authority | `metadata_policy_drafts` | 1 R / 4 KiB | `metadata_policy.create_metadata_policy_draft`; activation/draft readers | Immutable policy source is distinct from active state. |
| Canonical immutable authority | `metadata_policy_resolutions` | 3 R / 4 KiB | policy activation; proposal/admission/policy-impact readers | Per-generation, per-library resolved policy binds a profile to a library; it is not a draft or singleton. |
| Append-only audit/history | `review_case_transitions` | 2,060 R / 924 KiB | admission, pipeline and policy activation; lineage/history readers | Checkpointed transition history proves supersession, retirement and absence; the current pointer names only its latest consequence. |
| Append-only audit/history | `human_decisions` | 2 R / 4 KiB | `decisions.record_human_decision`; browse, history and effect readers | Owner decision, idempotency key and target fingerprint remain immutable audit authority. |
| Append-only audit/history | `human_decision_events` | 2 R / 4 KiB | `decisions.record_human_decision`; Activity/history readers | Event wording and immutable event identity are not interchangeable with the decision authority row. |
| Append-only audit/history | `automatic_decisions` | 3,847 R / 7,716 KiB | automatic projection; browse, safety and effect readers | Exact policy decision fact has independent idempotency and a per-case/destination uniqueness boundary. |
| Append-only audit/history | `cover_considerations` | 2,019 R / 147,220 KiB | `cover_considerations.record_cover_consideration`; cover readers | Immutable prepared candidate packet may be large; it is the dominant retained cover payload and cannot become a pointer. |
| Append-only audit/history | `cover_consideration_events` | 2,019 R / 1,352 KiB | cover preparation/outcome and stale retirement; cover/activity readers | Preparation, supersession, selection and stale facts are independently auditable. |
| Append-only audit/history | `field_effect_events` | 0 R / 4 KiB | field-effect transitions; Activity reader | Required append-only operational audit, empty in this report-only retained state. |
| Append-only audit/history | `field_effect_no_changes` | 71 R / 52 KiB | `record_current_field_effect_no_change`; Activity/history readers | Equal automatic candidate is a terminal durable fact, not an absent effect. |
| Append-only audit/history | `shadow_decision_outcomes` | 2,028 R / 18,028 KiB | shadow publication; policy/browse/history readers | Immutable evaluated reviewer outcome remains non-authoritative source evidence. |
| Current pointer/projection | `current_metadata_reports` | 2,026 R / 432 KiB | `reports.publish`; exact-file currentness and proposal readers | Key includes file identity, so it detects replacement of the same target item. |
| Current pointer/projection | `current_metadata_report_lineages` | 2,026 R / 300 KiB | `reports.publish`; target-lineage, activity and pipeline readers | Key omits file identity and gives one current report for target-level joins. It answers a different question from `current_metadata_reports`. |
| Current pointer/projection | `current_review_case_lineages` | 2,022 R / 816 KiB | admission, pipeline and policy activation; currentness/cover/effect readers | Latest transition plus checkpoint establishes one live case lineage and protects optimistic-currentness checks. |
| Current pointer/projection | `current_review_packages` | 2,022 R / 628 KiB | report publication, admission and pipeline; Library/Attention/selected-work readers | The owner-visible package projection carries report-only as well as case outcomes. It is intentionally separate from the lineage's transition witness. |
| Current pointer/projection | `current_cover_considerations` | 2,019 R / 356 KiB | cover preparation; cover readers | One current candidate per live case makes supersession/retry bounded while preserving packet history. |
| Current pointer/projection | `review_case_field_facts` | 32,448 R / 3,568 KiB | insert trigger from `review_cases`; Library/Attention count/page and admission readers | #802's derived current facts, including owner actionability, replace repeated case-document JSON walks. Open-time equivalence validation rejects drift. |
| Current pointer/projection | `shadow_outcome_field_facts` | 15,196 R / 3,660 KiB | insert trigger from `shadow_decision_outcomes`; Library/Attention and policy readers | #802's derived disposition facts replace repeated outcome-document JSON walks. Open-time equivalence validation rejects drift. |
| Operational/recovery | `review_preparation_scans` | 3 R / 4 KiB | discovery scheduling/completion; pipeline activity readers | One scan is the bounded discovery lifecycle parent, distinct from its many item work rows. |
| Operational/recovery | `review_preparation_work` | 2,053 R / 1,740 KiB | claim, advance, complete and fail pipeline actions; latest-work and recovery readers | Restart, attempt, fair claim and per-item state require a mutable work row; it is not audit history. |
| Operational/recovery | `review_preparation_absences` | 0 R / 4 KiB | discovery confirmation; pipeline transition reader | Bounded staged absence prevents an incomplete scan from tombstoning a lineage. |
| Operational/recovery | `review_preparation_connection_conditions` | 1 R / 4 KiB | connection recovery; claim/activity readers | Connection-scoped fault state pauses only affected work and has its own epoch. |
| Operational/recovery | `provider_recovery_outbox` | 2,060 R / 828 KiB | package cutover enqueue/acknowledgement; provider-recovery runner | Exact package hand-off is durable before external recovery enqueue; it cannot be folded into package state without losing replay/ack semantics. |
| Operational/recovery | `metadata_policy_transitions` | 1 R / 4 KiB | policy activation; transition status readers | One generation cutover state machine. |
| Operational/recovery | `metadata_policy_transition_items` | 0 R / 4 KiB | activation restart/settlement; transition readers | Per-item transition progress is separate from the policy's aggregate state. |
| Operational/recovery | `field_effects` | 0 R / 4 KiB | effect record/claim/transition; effect/activity readers | Mutable guarded-effect plan with a closed retry state machine. |
| Operational/recovery | `field_effect_actions` | 0 R / 4 KiB | reconciliation scheduler/claim; effect runner | Bounded action queue is distinct from plan state for restartable execution. |
| Operational/recovery | `field_effect_cleanups` | 0 R / 4 KiB | effect cleanup; activity reader | Cleanup completion is a separate durable recovery boundary. |
| Security/session | `review_sessions` | 4 R / 4 KiB | session create/authenticate/revoke; web authentication | Only token digests and expiry/revocation are retained; session state has no review identity role. Moved to the dedicated session database by #912. |
| Security/session | `review_login_failures` | 0 R / 4 KiB | failed-login throttle; authentication reader | Bounded global throttle history must not be conflated with sessions. Moved to the dedicated session database by #912. |
| Control/configuration | `metadata_policy_state` | 1 R / 4 KiB | policy activation/posture; all policy/currentness readers | Singleton selects active immutable policy and generation. |
| Control/configuration | `review_preparation_control` | 1 R / 4 KiB | pipeline pause/fair-claim/circuit state; claim readers | Singleton controls fair preparation scheduling, not policy authority. |
| Control/configuration | `automatic_safety_state` | 1 R / 4 KiB | safety pause/suspension; decision and Attention readers | Singleton safety gate is deliberately independent of policy selection. |
| Control/configuration | `field_effect_control` | 1 R / 4 KiB | field-effect pause; effect runner | Separate write-execution control state. |
| Control/configuration | `field_effect_suspensions` | 2 R / 4 KiB | field-effect failure handling; Attention/effect readers | Connection/destination capability state has a different key and recovery policy from global controls. |
| Control/configuration | `field_effect_authorities` | 0 R / 4 KiB | authority revocation/currentness; effect claim reader | Mutable authority witness for a decision after its immutable decision document is recorded. |
| Control/configuration | `field_effect_consents` | 0 R / 4 KiB | owner consent grant/revoke; effect claim reader | Consent is separately revocable and library scoped. |
| Control/configuration | `field_effect_manual_holds` | 0 R / 4 KiB | owner-decision return/release; pipeline/effect readers | Manual hold controls automatic return independently of policy or consent. |

## Exact production owner matrix

The offline `review-owner-decision-actions-v1` plan accepts only the frozen
pre-#830 four-column `review_case_field_facts` schema. It checks the literal
target against the installed build before filesystem access, then verifies the
literal source and target after opening the bound database before replacing only that
derived table and `review_cases_project_field_facts` trigger from
immutable case JSON. Canonical, provider, decision, shadow, operational, and
all other derived rows are digest-preserved.

`W` names the persistence method that writes the relation. `O` names the
owner-visible read path; a dash means the relation has no direct owner-visible
surface and is used only to protect a later operation. `I/A` distinguishes
open-time integrity (`schema._validate_schema_contents`, including exact object,
foreign-key and `integrity_check` validation) from offline adoption. Adoption's
`schema_adoption._application_table_digest` covers every non-derived application
table. Its `_validate_canonical_documents` additionally validates every
`document` column, while the two derived fact relations are separately
reconstructed and checked by `_validate_review_field_facts` during open-time
validation and adoption. This matrix was cross-checked against the persistence
code graph and the named source symbols.

| Table | W: production writer | O: owner-visible reader | I/A: additional exact check |
| --- | --- | --- | --- |
| `automatic_decisions` | `decisions._record_automatic_decision` | `review_projections._browse_decision_documents`; `review_cases` attention SQL | decision JSON is checked through `AutomaticDecision`; adoption also digests row values |
| `automatic_safety_state` | `decisions._set_automatic_safety_state` | `review_projections._automatic_attention_destinations`; `review_sql._AUTOMATIC_SAFETY_ATTENTION_SQL` | `decisions.automatic_safety_state` parses the singleton; adoption rebuilds `AutomaticSafetyState` |
| `cover_consideration_events` | `cover_considerations._record_cover_consideration_event` | `review_projections.cover_consideration_history` | canonical event document validation in adoption |
| `cover_considerations` | `cover_considerations.record_cover_consideration` | `review_projections.current_cover_consideration_document` | canonical consideration document validation in adoption |
| `current_cover_considerations` | `cover_considerations.record_cover_consideration` | `review_projections.current_cover_consideration_document` | FK/current-lineage join protects selection |
| `current_metadata_report_lineages` | `reports.publish` | `review_projections`, `field_effect_activity` and pipeline currentness reads | report identity checked by `schema._validate_report_row` |
| `current_metadata_reports` | `reports.publish` | `review_projections`; proposal/admission currentness reads | report identity checked by `schema._validate_report_row` |
| `current_review_case_lineages` | `review_cases.admit_review_case`; `review_preparation`; `metadata_policy.activate_metadata_policy` | `review_projections.read_current_review_case` | `review_cases._validated_review_pointer` rechecks transition/case/package consistency |
| `current_review_packages` | `reports.publish`; `review_cases.admit_review_case`; `review_preparation.advance_review_pipeline_action` | `review_projections.read_review_library` and `read_review_attention` | `review_cases.current_review_package` validates exact projection shape |
| `field_effect_actions` | `field_effects.schedule_field_effect_reconciliation` and claim/completion methods | `field_effect_activity` Activity projection | action row is parsed/validated by field-effect state validation |
| `field_effect_authorities` | `field_effects.record_field_effect` and authority revocation | `field_effects.field_effect_authority_source` (pre-claim protection) | field-effect state validation |
| `field_effect_cleanups` | `field_effects.complete_field_effect_cleanup` scheduling/settlement | `field_effect_activity` Activity projection | field-effect state validation |
| `field_effect_consents` | `field_effects.grant_field_effect_consents` and revoke methods | `field_effects.current_field_effect_consent` (pre-claim protection) | field-effect state validation |
| `field_effect_control` | `field_effects.set_field_effect_paused` | `field_effect_activity.operational_snapshot` | field-effect state validation |
| `field_effect_events` | `field_effects._append_field_effect_event` | `field_effect_activity` Activity projection | canonical event document validation in adoption |
| `field_effect_manual_holds` | `field_effects` manual-return/reconciliation methods | `field_effect_activity` and `review_preparation._has_pending_field_effect_manual_return` | field-effect state validation |
| `field_effect_no_changes` | `field_effects.record_current_field_effect_no_change` | `field_effect_activity` Activity projection | exact no-change identity is checked by field-effect state validation |
| `field_effect_suspensions` | `field_effects.transition_field_effect` | `review_projections._field_effect_capability_suspended_destinations` and `_field_effect_capability_suspension_causes` | field-effect state validation; no document-authority substitution |
| `field_effects` | `field_effects.record_field_effect`, `claim_field_effect`, `transition_field_effect` | `field_effect_activity` Activity/pending projection | field-effect state validation |
| `human_decision_events` | `decisions.record_human_decision` | `field_effect_activity` and review history | canonical event document validation in adoption |
| `human_decisions` | `decisions.record_human_decision` | `review_projections._browse_decision_documents`; `decisions.get_review_case_history` | `HumanDecision.from_json` envelope check and adoption document validation |
| `metadata_policy_drafts` | `metadata_policy.create_metadata_policy_draft` | policy draft/preview routes | `schema._validate_metadata_policy_documents` |
| `metadata_policy_resolutions` | `metadata_policy.activate_metadata_policy` | `metadata_policy.current_resolved_metadata_policy`; policy impact reader | resolved policy is parsed against its generation |
| `metadata_policy_state` | `metadata_policy.activate_metadata_policy`, `enter_shadow_preview`, `activate_qualified_automatic` | policy routes and all policy currentness reads | `schema._validate_metadata_policy_documents` |
| `metadata_policy_transition_items` | `metadata_policy.activate_metadata_policy` and transition settlement | policy activation status | FK/state checks; no owner-visible standalone DTO |
| `metadata_policy_transitions` | `metadata_policy.activate_metadata_policy` and refresh | policy activation status and Activity | FK/state checks |
| `metadata_report_cover_references` | `reports.publish` | cover artifact/packet readers | report publication verifies the complete reference set |
| `metadata_reports` | `reports.publish` | `review_projections`, `reports.get`, Activity | `_validate_report_row`; adoption parses every canonical report |
| `proposal_sets` | `proposal_sets.publish_proposal` | `proposal_sets.get_proposal`; admission | `_validate_proposal_row`; adoption parses canonical proposal documents |
| `provider_recovery_outbox` | `recovery_outbox.enqueue`, `complete_provider_recovery_outbox` | no direct owner DTO; recovery runner drain | `_validate_provider_recovery_outbox_row` on open and drain |
| `review_case_field_facts` | `review_cases_project_field_facts` trigger | `review_projections` and `review_cases` Library/Attention SQL | `_validate_review_field_facts` and adoption reconstruct it |
| `review_case_transitions` | `review_cases.admit_review_case`; pipeline and policy activation | `decisions.get_review_case_history`; Activity | transition shape is checked by current-pointer reads |
| `review_cases` | `review_cases.admit_review_case` | `review_projections`, `review_cases.get_review_case` | `ReviewCase.from_json`; adoption parses every case |
| `review_login_failures` | `review_sessions` failed-login throttle | authentication only | bounded row shape under exact schema |
| `review_preparation_absences` | `review_preparation.complete_review_discovery` | preparation status only | FK and scan/currentness checks |
| `review_preparation_connection_conditions` | `review_scans` connection recovery/hold methods | preparation status and Activity | closed connection-condition row validation |
| `review_preparation_control` | `review_scans.set_review_pipeline_paused`; `review_preparation` fair claim | preparation status and Activity | singleton/closed state checks |
| `review_preparation_scans` | `review_scans.request_review_preparation`, discovery completion; `metadata_policy.activate_metadata_policy`; field-effect manual-return recovery | preparation status and Activity | scan state/currentness checks |
| `review_preparation_work` | `review_preparation` claim/advance/complete/fail methods | latest terminal status in `review_projections.read_review_library` | work state/attempt/currentness checks |
| `review_sessions` | `review_sessions.create_review_session`, rotate/revoke | web authentication only | digest/expiry row validation |
| `shadow_decision_outcomes` | `metadata_reports.persist_shadow_outcome` | `review_projections._shadow_abstention_destinations`; bounded history | `ShadowOutcomeSet` envelope checks and adoption document validation |
| `shadow_outcome_field_facts` | `shadow_outcomes_project_field_facts` trigger | `review_sql` Attention predicates and `review_projections` | `_validate_review_field_facts` and adoption reconstruct it |

### All-occurrence ownership cross-check

This compact cross-check separates all production occurrence classes rather than
treating a read as ownership. `S` is the mandatory exact-schema/open integrity
check; `D` is `schema_adoption._application_table_digest`; `C` is
`schema_adoption._validate_canonical_documents`; and `F` is
`schema._validate_review_field_facts` plus adoption reconstruction. Thus each
row's validation and adoption column is explicit even where the common check is
the only occurrence. Names are module-local persistence symbols.

| Table | Writers | Internal / operational reads | Owner-visible reads | Validation | Adoption |
| --- | --- | --- | --- | --- | --- |
| `automatic_decisions` | `decisions._record_automatic_decision` | `decisions`; safety checks | `review_projections._browse_decision_documents` | `S`, automatic envelope | `D,C` |
| `automatic_safety_state` | `decisions._set_automatic_safety_state` | `decisions.automatic_safety_state` | `review_projections._automatic_attention_destinations` | `S`, singleton parse | `D` |
| `cover_consideration_events` | `cover_considerations._record_cover_consideration_event` | cover replay/history | `review_projections.cover_consideration_history` | `S` | `D,C` |
| `cover_considerations` | `cover_considerations.record_cover_consideration` | cover replay/selection | `review_projections.current_cover_consideration_document` | `S` | `D,C` |
| `current_cover_considerations` | `cover_considerations.record_cover_consideration` | selection currentness | `review_projections.current_cover_consideration_document` | `S`, FK/current join | `D` |
| `current_metadata_report_lineages` | `reports.publish` | pipeline/activity currentness | `review_projections` | `S,_validate_report_row` | `D` |
| `current_metadata_reports` | `reports.publish` | proposal/admission currentness | `review_projections` | `S,_validate_report_row` | `D` |
| `current_review_case_lineages` | `review_cases.admit_review_case`; `review_preparation`; policy activation | `review_cases._validated_review_pointer` | `review_projections.read_current_review_case` | `S`, pointer check | `D` |
| `current_review_packages` | `reports.publish`; `review_cases.admit_review_case`; `review_preparation.advance_review_pipeline_action` | package cutover/recovery | `review_projections.read_review_library`, `read_review_attention` | `S`, package shape | `D` |
| `field_effect_actions` | `field_effects.schedule_field_effect_reconciliation`, claim/complete | field-effect runner | `field_effect_activity` | `S`, state check | `D,C` |
| `field_effect_authorities` | `field_effects.record_field_effect`, revoke | `field_effect_authority_source` | — | `S`, state check | `D,C` |
| `field_effect_cleanups` | `field_effects.complete_field_effect_cleanup` | cleanup runner | `field_effect_activity` | `S`, state check | `D,C` |
| `field_effect_consents` | `field_effects.grant_field_effect_consents`, revoke | `current_field_effect_consent` | — | `S`, state check | `D,C` |
| `field_effect_control` | `field_effects.set_field_effect_paused` | effect runner | `field_effect_activity.operational_snapshot` | `S`, singleton state | `D` |
| `field_effect_events` | `field_effects._append_field_effect_event` | effect history | `field_effect_activity` | `S` | `D,C` |
| `field_effect_manual_holds` | field-effect manual-return methods | `review_preparation._has_pending_field_effect_manual_return` | `field_effect_activity` | `S`, state check | `D,C` |
| `field_effect_no_changes` | `field_effects.record_current_field_effect_no_change` | effect reconciliation | `field_effect_activity` | `S`, identity check | `D` |
| `field_effect_suspensions` | `field_effects.transition_field_effect` | capability checks | `review_projections._field_effect_capability_suspended_destinations` | `S`, state check | `D,C` |
| `field_effects` | record/claim/transition field-effect methods | field-effect runner | `field_effect_activity` | `S`, state check | `D,C` |
| `human_decision_events` | `decisions.record_human_decision` | history/effect authority | `field_effect_activity` | `S` | `D,C` |
| `human_decisions` | `decisions.record_human_decision` | `decisions.get_review_case_history` | `review_projections._browse_decision_documents` | `S`, `HumanDecision.from_json` | `D,C` |
| `metadata_policy_drafts` | `metadata_policy.create_metadata_policy_draft` | activation/preview | policy draft routes | `S,_validate_metadata_policy_documents` | `D,C` |
| `metadata_policy_resolutions` | `bind_initial_metadata_policy_resolution`; `activate_metadata_policy` | `current_resolved_policy_id`; `current_resolved_metadata_policy`; `metadata_policy_resolution_for_preview`; `metadata_policy_preview_baseline`; `enter_shadow_preview`; `_policy_destinations_for`; `SQLiteMetadataReportRepository._validate_shadow_outcome_row` | `field_effect_activity` policy/currentness projection | `S,_validate_metadata_policy_documents` | `D,C` |
| `metadata_policy_state` | `activate_metadata_policy`; `enter_shadow_preview`; `activate_qualified_automatic` | policy currentness/activation | policy routes; `field_effect_activity` | `S,_validate_metadata_policy_documents` | `D` |
| `metadata_policy_transition_items` | activation/settlement | transition restart | — | `S`, FK/state | `D` |
| `metadata_policy_transitions` | activation/refresh | transition restart/status | policy status; `field_effect_activity` | `S`, FK/state | `D` |
| `metadata_report_cover_references` | `reports.publish` | cover packet assembly | cover artifact readers | `S`, complete set | `D` |
| `metadata_reports` | `reports.publish` | `reports.get`, activity | `review_projections` | `S,_validate_report_row` | `D,C` |
| `proposal_sets` | `proposal_sets.publish_proposal` | admission/policy | `proposal_sets.get_proposal` | `S,_validate_proposal_row` | `D,C` |
| `provider_recovery_outbox` | `recovery_outbox.enqueue`, complete | recovery drain | — | `S,_validate_provider_recovery_outbox_row` | `D` |
| `review_case_field_facts` | `review_cases_project_field_facts` trigger | admission predicates | `review_projections` Library/Attention | `S,F` | `F` |
| `review_case_transitions` | admission/pipeline/policy activation | current-pointer recovery | case history, `field_effect_activity` | `S`, transition shape | `D,C` |
| `review_cases` | `review_cases.admit_review_case` | admission/currentness | `review_projections`, `get_review_case` | `S`, `ReviewCase.from_json` | `D,C` |
| `review_login_failures` | failed-login throttle | authentication throttle | — | `S`, row shape | `D` |
| `review_preparation_absences` | `complete_review_discovery` | discovery finalisation | preparation status | `S`, FK/currentness | `D` |
| `review_preparation_connection_conditions` | connection recovery/hold methods | fair claim/recovery | preparation status, `field_effect_activity` | `S`, state check | `D` |
| `review_preparation_control` | `set_review_pipeline_paused`; fair claim | scheduling/claim | preparation status, `field_effect_activity` | `S`, singleton state | `D` |
| `review_preparation_scans` | `request_review_preparation`, completion; `metadata_policy.activate_metadata_policy`; field-effect manual-return recovery | scan/recovery | preparation status, `field_effect_activity` | `S`, scan state | `D` |
| `review_preparation_work` | claim/advance/complete/fail methods | pipeline/recovery | `review_projections.read_review_library` | `S`, work state | `D` |
| `review_sessions` | create/rotate/revoke session | authentication | — | `S`, digest/expiry | `D` |
| `shadow_decision_outcomes` | `metadata_reports.persist_shadow_outcome` | automatic decision projection/history | `review_projections._shadow_abstention_destinations` | `S`, `ShadowOutcomeSet` | `D,C` |
| `shadow_outcome_field_facts` | `shadow_outcomes_project_field_facts` trigger | policy predicates | `review_projections` Attention | `S,F` | `F` |

## Named and exact automatic index ledger

The retained backup reconciles exactly: application table b-trees use
385,232,896 bytes; seven named indexes use 5,009,408 bytes; the exact validated
automatic indexes use 7,872,512 bytes; and `sqlite_schema` uses 53,248 bytes.
Their sum is 398,168,064 bytes, with zero freelist and zero unassigned bytes.
`Entries` is the parent table's retained row count for each complete ordinary
index. For the two partial cover indexes it is the eligible-row count and
`dbstat.ncell`, both 6,216 in this snapshot, rather than the 13,446 rows in
their parent. `WITHOUT ROWID` primary keys are the table b-tree, not a separate
automatic index; `field_effect_no_changes` consequently exposes only its
distinct event automatic index.
`WITHOUT ROWID` primary keys are the table b-tree, not a separate automatic
index; `field_effect_no_changes` consequently exposes only its distinct event
automatic index.

| Exact index | Parent | Entries | KiB |
| --- | --- | ---: | ---: |
| `metadata_report_cover_references_artifact_id` | `metadata_report_cover_references` | 6,216 | 560 |
| `metadata_report_cover_references_preview_digest` | `metadata_report_cover_references` | 6,216 | 508 |
| `review_preparation_work_aged_claim` | `review_preparation_work` | 2,053 | 100 |
| `review_preparation_work_lineage_history` | `review_preparation_work` | 2,053 | 328 |
| `review_preparation_work_recent_claim` | `review_preparation_work` | 2,053 | 148 |
| `shadow_decision_outcomes_case_history` | `shadow_decision_outcomes` | 2,028 | 360 |
| `shadow_outcome_field_facts_case` | `shadow_outcome_field_facts` | 15,196 | 2,888 |
| `sqlite_autoindex_automatic_decisions_1` | `automatic_decisions` | 3,847 | 408 |
| `sqlite_autoindex_automatic_decisions_2` | `automatic_decisions` | 3,847 | 320 |
| `sqlite_autoindex_automatic_decisions_3` | `automatic_decisions` | 3,847 | 404 |
| `sqlite_autoindex_cover_consideration_events_1` | `cover_consideration_events` | 2,019 | 232 |
| `sqlite_autoindex_cover_considerations_1` | `cover_considerations` | 2,019 | 216 |
| `sqlite_autoindex_current_cover_considerations_1` | `current_cover_considerations` | 2,019 | 188 |
| `sqlite_autoindex_current_cover_considerations_2` | `current_cover_considerations` | 2,019 | 216 |
| `sqlite_autoindex_current_metadata_report_lineages_1` | `current_metadata_report_lineages` | 2,026 | 164 |
| `sqlite_autoindex_current_metadata_reports_1` | `current_metadata_reports` | 2,026 | 324 |
| `sqlite_autoindex_current_review_case_lineages_1` | `current_review_case_lineages` | 2,022 | 160 |
| `sqlite_autoindex_current_review_packages_1` | `current_review_packages` | 2,022 | 164 |
| `sqlite_autoindex_field_effect_actions_1` | `field_effect_actions` | 0 | 4 |
| `sqlite_autoindex_field_effect_actions_2` | `field_effect_actions` | 0 | 4 |
| `sqlite_autoindex_field_effect_authorities_1` | `field_effect_authorities` | 0 | 4 |
| `sqlite_autoindex_field_effect_cleanups_1` | `field_effect_cleanups` | 0 | 4 |
| `sqlite_autoindex_field_effect_consents_1` | `field_effect_consents` | 0 | 4 |
| `sqlite_autoindex_field_effect_events_1` | `field_effect_events` | 0 | 4 |
| `sqlite_autoindex_field_effect_events_2` | `field_effect_events` | 0 | 4 |
| `sqlite_autoindex_field_effect_manual_holds_1` | `field_effect_manual_holds` | 0 | 4 |
| `sqlite_autoindex_field_effect_no_changes_2` | `field_effect_no_changes` | 71 | 20 |
| `sqlite_autoindex_field_effect_suspensions_1` | `field_effect_suspensions` | 2 | 4 |
| `sqlite_autoindex_field_effects_1` | `field_effects` | 0 | 4 |
| `sqlite_autoindex_human_decision_events_1` | `human_decision_events` | 2 | 4 |
| `sqlite_autoindex_human_decision_events_2` | `human_decision_events` | 2 | 4 |
| `sqlite_autoindex_human_decisions_1` | `human_decisions` | 2 | 4 |
| `sqlite_autoindex_human_decisions_2` | `human_decisions` | 2 | 4 |
| `sqlite_autoindex_metadata_policy_drafts_1` | `metadata_policy_drafts` | 1 | 4 |
| `sqlite_autoindex_metadata_policy_resolutions_1` | `metadata_policy_resolutions` | 3 | 4 |
| `sqlite_autoindex_metadata_policy_transition_items_1` | `metadata_policy_transition_items` | 0 | 4 |
| `sqlite_autoindex_metadata_policy_transitions_1` | `metadata_policy_transitions` | 1 | 4 |
| `sqlite_autoindex_metadata_policy_transitions_2` | `metadata_policy_transitions` | 1 | 4 |
| `sqlite_autoindex_metadata_report_cover_references_1` | `metadata_report_cover_references` | 13,446 | 2,440 |
| `sqlite_autoindex_metadata_reports_1` | `metadata_reports` | 2,064 | 184 |
| `sqlite_autoindex_metadata_reports_2` | `metadata_reports` | 2,064 | 484 |
| `sqlite_autoindex_proposal_sets_1` | `proposal_sets` | 2,060 | 188 |
| `sqlite_autoindex_provider_recovery_outbox_1` | `provider_recovery_outbox` | 2,060 | 200 |
| `sqlite_autoindex_review_case_transitions_1` | `review_case_transitions` | 2,060 | 220 |
| `sqlite_autoindex_review_case_transitions_2` | `review_case_transitions` | 2,060 | 164 |
| `sqlite_autoindex_review_cases_1` | `review_cases` | 2,028 | 188 |
| `sqlite_autoindex_review_login_failures_1` | `review_login_failures` | 0 | 4 |
| `sqlite_autoindex_review_preparation_absences_1` | `review_preparation_absences` | 0 | 4 |
| `sqlite_autoindex_review_preparation_connection_conditions_1` | `review_preparation_connection_conditions` | 1 | 4 |
| `sqlite_autoindex_review_preparation_scans_1` | `review_preparation_scans` | 3 | 4 |
| `sqlite_autoindex_review_preparation_work_1` | `review_preparation_work` | 2,053 | 204 |
| `sqlite_autoindex_review_preparation_work_2` | `review_preparation_work` | 2,053 | 288 |
| `sqlite_autoindex_review_sessions_1` | `review_sessions` | 4 | 4 |
| `sqlite_autoindex_shadow_decision_outcomes_1` | `shadow_decision_outcomes` | 2,028 | 216 |

The following public-safe, mechanical check was run against that one disposable
backup. It fails unless all 42 table extents, all 7 named and 47 automatic
indexes, `sqlite_schema`, freelist, and the file total reconcile in one snapshot.
The caller supplies only a disposable copy and removes it after inspection.

```text
uv run python - /path/to/disposable.sqlite3 <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
pages = dict(db.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name"))
objects = list(db.execute("SELECT type, name FROM sqlite_schema WHERE type IN ('table','index')"))
tables = [name for kind, name in objects if kind == 'table' and name != 'sqlite_schema']
named = [name for kind, name in objects if kind == 'index' and not name.startswith('sqlite_autoindex_')]
automatic = [name for kind, name in objects if kind == 'index' and name.startswith('sqlite_autoindex_')]
table_bytes = sum(pages[name] for name in tables)
named_bytes = sum(pages[name] for name in named)
automatic_bytes = sum(pages[name] for name in automatic)
schema_bytes = pages['sqlite_schema']
total = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
freelist = db.execute('PRAGMA freelist_count').fetchone()[0]
assert (len(tables), len(named), len(automatic)) == (42, 7, 47)
assert (table_bytes, named_bytes, automatic_bytes, schema_bytes, total, freelist) == (385232896, 5009408, 7872512, 53248, 398168064, 0)
assert table_bytes + named_bytes + automatic_bytes + schema_bytes == total
PY
```

## Bounded write workload

The audit runs this repository-owned command on eight existing public-safe
tests, each using a clean disposable SQLite database:

```text
uv run python scripts/audit_sqlite_row_deltas.py
```

The instrument records the final `count(*)` of every exact-schema table on each
connection close, subtracts a freshly created exact-schema baseline, and sums
the per-test database deltas. It does not use `set_trace_callback`: SQLite 3.53
in the qualified runtime exposes neither an update nor preupdate hook. A row
delta is durable committed cardinality evidence, unaffected by trigger callback
amplification, but it cannot show update-only changes, deleted-then-reinserted
rows, statement attempts, or transactions rolled back before close. No
statement-attempt telemetry was retained or used.

All nodes passed in 0.93 seconds; the bounded command elapsed 1.03 seconds.
Positive deltas below name every affected relation (unlisted relations are zero
relative to the clean baseline):

| Exact test node and operation | Final committed row delta |
| --- | --- |
| `test_one_work_progresses_through_every_persisted_stage` — preparation | reports/proposals/cases/transitions/current report+lineage+package/outbox/scans/work: 1 each; case facts: 16 |
| `test_recovery_successor_uses_normal_projection_and_atomic_package_cutover` — recovery successor | reports/proposals/cases/transitions/outbox/shadow outcomes: 2 each; current report+lineage+package/scans/work: 1 each; case facts: 32; shadow facts: 12 |
| `test_sqlite_human_decision_is_atomic_idempotent_and_reopens_exactly` — human decision | `human_decisions` and `human_decision_events`: 2 each; reports: 2; current reports: 2; proposals/cases/current lineage+package/transitions: 1 each; case facts: 16 |
| `test_atomic_cutover_rewinds_existing_evidence_and_completes_after_restart` — policy cutover | policy drafts/resolutions/transitions: 2 each; transition items: 3; reports/current reports/current lineages/current packages: 2 each; proposals: 3; cases: 3; transitions: 6; case facts: 48; outbox: 2; scans/work: 1 each |
| `test_verified_sibling_survives_failure_and_restart` — field effect | authorities/consents/effects: 2 each; field-effect events: 11 |
| `test_selected_original_reaches_verified_guarded_fake_service_application` — manual cover selection | reports/proposals/cases/current reports+lineages+packages/transitions: 1 each; report cover references: 2; cover considerations: 2; cover events: 6; current cover considerations: 1; human decisions/events: 2 each; field-effect authorities/effects/cleanups: 2 each; consents: 1; effect events: 6; case facts: 16 |
| `test_preview_automatic_matrix_preserves_human_policy_and_records_outcomes` — shadow preview | policy drafts/resolutions/transitions: 5 each; reports/proposals/cases/current reports+lineages+packages/transitions/shadow outcomes: 5 each; case facts: 80; shadow facts: 32 |
| `test_activation_replay_after_ack_failure_keeps_recovery_state_idempotent` — provider recovery | reports/current reports/current packages/outbox: 1 each |

This confirms bounded operation shape only. The test suite does not retain a
production observation window, and these public-safe tests are not a rate model
for the retained library. In particular, trigger-derived fact deltas are durable
rows in the same transaction, not trace callback counts.

## Foreign-key map

The exact schema has the following application referential edges. Any omitted
table intentionally has no relational parent; its shape is validated by its
own closed checks and/or domain-document validation.

| Child table | Referenced authority |
| --- | --- |
| `automatic_decisions` | `review_cases` |
| `cover_considerations` | `review_cases`, `metadata_reports`, self (supersedes) |
| `cover_consideration_events` | `cover_considerations`, `review_cases` |
| `current_cover_considerations` | `review_cases`, `cover_considerations` |
| `current_metadata_reports`, `current_metadata_report_lineages` | `metadata_reports` |
| `current_review_case_lineages` | `proposal_sets`, `metadata_reports`, `review_cases`, `review_case_transitions` |
| `current_review_packages` | `proposal_sets`, `metadata_reports`, `review_cases` |
| `field_effect_actions`, `field_effect_cleanups`, `field_effect_events` | `field_effects` |
| `field_effect_manual_holds` | `review_cases` |
| `field_effect_no_changes` | `automatic_decisions`, `review_cases`, `metadata_reports` |
| `human_decisions` | `review_cases` |
| `human_decision_events` | `human_decisions`, `review_cases` |
| `metadata_policy_state`, `metadata_policy_transitions` | `metadata_policy_drafts` |
| `metadata_policy_transition_items` | `metadata_policy_transitions`, `metadata_reports`, `review_preparation_work` |
| `metadata_report_cover_references`, `proposal_sets` | `metadata_reports` |
| `provider_recovery_outbox` | `proposal_sets`, `metadata_reports`, `review_cases` |
| `review_cases` | `proposal_sets`, `metadata_reports` |
| `review_case_field_facts` | `review_cases` |
| `review_case_transitions` | `metadata_reports`, `proposal_sets`, `review_cases` (from/to) |
| `review_preparation_absences` | `review_preparation_scans`, `proposal_sets`, `metadata_reports`, `review_cases` |
| `review_preparation_work` | `review_preparation_scans`, `metadata_reports` (report/recovery source), `proposal_sets`, `review_cases` |
| `shadow_decision_outcomes` | `review_cases`, `metadata_reports`, `proposal_sets` |
| `shadow_outcome_field_facts` | `shadow_decision_outcomes`, `review_cases` |

## Exact indexes and triggers

The seven named indexes are all live production access paths: cover artifact
and preview lookups use `metadata_report_cover_references_artifact_id` and
`metadata_report_cover_references_preview_digest`; fair claims use
`review_preparation_work_aged_claim` and
`review_preparation_work_recent_claim`; latest-work status uses
`review_preparation_work_lineage_history`; bounded outcome history uses
`shadow_decision_outcomes_case_history`; and #802 Library/Attention predicates
use `shadow_outcome_field_facts_case`. Primary and unique keys provide the
remaining exact indexes; the schema validates their SQLite automatic-index
objects too.

Twenty-eight triggers reject update and delete on the fourteen immutable/audit
tables: `metadata_reports`, `metadata_report_cover_references`, `proposal_sets`,
`metadata_policy_drafts`, `review_cases`, `review_case_transitions`,
`human_decisions`, `human_decision_events`, `cover_considerations`,
`cover_consideration_events`, `automatic_decisions`, `field_effect_events`,
`field_effect_no_changes`, and `shadow_decision_outcomes`. Two additional
after-insert triggers, `review_cases_project_field_facts` and
`shadow_outcomes_project_field_facts`, populate #802's derived facts in the
same transaction as their canonical documents. Open-time validation compares
both derived relations to their canonical JSON, so a partial write, manual
alteration, or interrupted projection fails closed rather than exposing stale
owner-visible truth.

## Required separations

Every cluster that shares an identifier, document, or current/history relation is
classified here. `Required` means neither a view nor a merged aggregate meets
the stated invariant; `view-derived` means the stored relation is a
same-transaction, reconstructible read projection; no cluster is `redundant`.
`—` marks an invariant that does not apply rather than leaving it implicit.

| Cluster | Classification | Transaction | Rollback | Restart | Idempotency | Optimistic-currentness | Audit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| reports / cover references | Required | `reports.publish` | same publication | replay verifies complete set | report identity | report only | report document |
| current reports / lineages | Required | `reports.publish` | same publication | re-read both | publication replay | file vs target key | — |
| reports / proposals / cases | Required | publish then admit | stale report/policy aborts | immutable reread | content identities | external pointers | immutable documents |
| cases / transitions | Required | admission/pipeline | pointer+transition atomic | latest transition | checkpoint key | transition witness | transition history |
| transitions / lineages / packages | Required | package cutover | all members atomic | package/lineage validation | cutover key | lineage pointer | transition only |
| cases / case facts | View-derived | insert trigger | same insert | open-time equivalence | case identity | — | case document |
| shadow outcomes / shadow facts | View-derived | insert trigger | same insert | open-time equivalence | outcome identity | — | outcome document |
| shadow outcomes / automatic decisions | Required | each authority command | local command | reread authority | decision key | — | both immutable facts |
| human decisions / events | Required | `record_human_decision` | same command | history reread | decision key | decision only | event/history |
| decisions / no-change / effect plans | Required | each state command | command scope | plan/action recovery | plan/action keys | decision authority | events/no-change |
| cover considerations / current pointer / events | Required | prepare/select | same command | current/event replay | consideration key | expected supersedes | events |
| cover references / considerations | Required | report publish vs prepare | owning command | owning recovery | report vs consideration identity | — | respective documents/events |
| policy drafts / state / resolutions / transitions / items | Required | activation | all touched policy rows | settle item rows | generation/item key | generation | transitions |
| singleton controls: policy state / preparation control / safety state / field-effect control | Required | independent control command | command scope | singleton reread | command-specific | — | — |
| scans / work / absences / connection conditions / preparation control | Required | discovery/claim | command scope | work reclaim | scan/work key | work/absence key | — |
| packages / recovery outbox | Required | atomic cutover+enqueue | same transaction | drain undelivered | outbox key | package pointer | delivery state |
| effect control / suspensions / authorities / consents / holds | Required | independent control command | command scope | state reread | scope key | authority/consent currentness | — |
| effects / actions / cleanups / events | Required | plan/action command | command scope | claim/retry | plan/action key | plan state | events |
| sessions / login failures | Required | authentication command | command scope | bounded state | — | — | — |

The sessions / login-failures cluster remains one aggregate, and #912 moved
it whole into `private/review-sessions.sqlite` under ADR-0059; its invariants
above are unchanged, now proven against the session database.

## #800 and #802 assessment

#800's two read indexes remain minimum-sufficient: the retained snapshot shows
the lineage-history index at 328 KiB and outcome-history index at 360 KiB, small
next to the histories they bound. #802's two fact tables add 7,228 KiB total
at this scale and replace the exact repeated `json_each` predicates in the
Library and Attention count/page paths. Their after-insert projection and
open-time equivalence check make canonical and derived state transactionally
exact; they are not a cache, background rebuild, or alternate authority.

No existing current table is replaced by #802. `current_review_packages` still
selects the current report/case package, while the fact relations answer the
per-destination classification questions that the package's canonical documents
would otherwise answer through repeated JSON walks. The remaining `json_each`
use over the small automatic-safety category document is intentionally retained:
it is a singleton closed control document, not a per-work canonical scan.

## Outcome and follow-up boundary

No safe consolidation was identified. Each apparent duplication has a current
production consumer and one of: an immutable-authority versus mutable-current
boundary, a distinct FK/key scope, independent restart/idempotency behaviour,
or append-only audit evidence. Removing a relation would either make current
reads recompute authoritative JSON, lose a transaction-local optimistic check,
or introduce a generic type-switched state table heavier than the tables it
replaces.

Accordingly this audit creates no follow-up issue and makes no schema change.
Any future proposal must first name the exact tables it removes, the single
transaction that preserves result equivalence, and proof for rollback, restart,
stale currentness, idempotency, audit retention, exact-schema rejection, and
the retained browse query plan. Until then, ADR-0013's absent-or-exact-current
rule remains unchanged.
