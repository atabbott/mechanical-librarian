# ADR-0091: Decode every browser response and report uncertain writes truthfully

- Status: proposed
- Date: 2026-11-01
- Owners: project maintainers
- Issues: #1577 (stage 1), #1571 (client half of uncertain outcomes), #1669
  (stage 2b, the schema states every single-field check), #1684 (stage 3, the
  decoders replace the hand-written checks), #1728 (stage 4, every JSON answer
  is decoded and the decode gate is retired), #1738 (stage 5, the policy
  preview-mode API and browser names say "preview" instead of "shadow")
- Amends: ADR-0043 (HTTP module and drift), ADR-0044 (build identity delivery)
- Supersedes: none
- Superseded by: none

## Context

The browser's HTTP module restates every server response shape by hand. The
server's response models are strict and closed, but the browser checks them
only where a mapper happens to look. Two further gaps decide what the owner is
told:

- A browser tab running the previous build is stopped only when it writes.
  Reads were never checked against the installed build, so an old tab kept
  rendering responses it may no longer understand.
- An unsafe request whose write committed could still be reported as refused:
  a fault after the commit answered with a 4xx, or a caller showed "nothing
  changed" for every error.

## Decision

1. **Generated decoders.** An in-repository generator emits response decoders
   from the OpenAPI document, with no runtime dependency, and the HTTP module
   decodes every JSON response through them. Generated files are not
   committed. Delivered in later stages of the same project, one response
   family at a time (#1684). A read whose 2xx body fails decoding is a
   `ResponseDecodeError`: a 502 `invalid_response` that carries the
   operation, the status, and the decoder's reason and pointer as properties,
   never in its message.
2. **Required properties.** Every response property is required except a
   pinned list of omissions. Delivered with the decoders.
3. **The HTTP module keeps** transport, view mapping, and the checks a schema
   cannot state, each named.
4. **Uncertain outcomes.** An unsafe request whose outcome the browser cannot
   know is reported as possibly applied, never as refused, and the caller
   reloads the authoritative state. The browser half is #1571's
   `uncertainWriteOutcome` and `PRE_CALL_SERVER_CODES`: a lost
   acknowledgement, a 5xx not proven to be raised before the handler ran, or
   a 2xx that fails decoding or a named check. That last is an
   `UncertainMutationError`, a kind of lost acknowledgement that keeps the
   decoder's reason and pointer, or `invariant` and the check's code. Only a
   command marked as writing nothing is a `ResponseDecodeError` instead; a
   command that is merely safe to resend after a CSRF renewal still writes,
   so it stays uncertain. Every operation is decoded: a 204 is accepted only
   from an operation that documents no JSON answer, and any other 2xx answer
   its operation does not document fails the same way. An answer that is not
   JSON, or a lost network call, is a lost acknowledgement, or for a command
   that writes nothing a failure of that command (a 502, or a
   `NetworkUnavailableError` when the network is lost). Only a lost network
   call and an answer that is not JSON are converted this way; a defect in a
   decoder or mapper is not converted.
   The server half is that no failure after a committed write is answered
   with a 4xx. `tests/unit/test_unsafe_outcome_inventory.py` pins, for every
   unsafe `/api/v1` route, each error status and code and the latest point it
   can be raised, and fails for an unclassified route. A decision's same-key
   Retry is the one place a repeat is offered, because the decision route
   replays a recorded decision instead of recording a second one. An
   acknowledgement the browser cannot read is reported as uncertain and the
   case is refreshed, because a same-key Retry would replay the same
   unreadable acknowledgement.
5. **Build identity on authenticated responses.** The server sets
   `x-ml-build-identity` on `/api/v1` responses to requests that authenticated
   an owner session, and the browser compares it on every response, read or
   write. A mismatch raises the shell's reload notice, and a route that fails
   on it offers "Reload the current application" instead of another try.
   - **Disclosure rule.** The identity is a non-secret version gate, not a
     credential. It is disclosed only to the audience that already receives
     it in the authenticated shell.
   - It is never sent on a 401, on `/api/v1/session/login`, in asset-recovery
     mode, on the trust boundary's early 400 and 403 answers, on a body-limit
     413, or to a request without a valid session. An unhandled 500 carries
     no header.
   - A route marks the request once `_require_session` authenticates it. An
     unmatched `/api/v1` path is checked with one session read and no write.

## Mapper checks the schema cannot state (X)

Since #1669 the OpenAPI document states every single-field response check the
HTTP module makes, so the generated decoders enforce it. What remains
hand-written (decision 3) is listed here: a check relating two or more fields
(cross-field), an order or position (positional), an identity across items or
documents (identity), a check whose only owner is persisted (persisted), or one
that shares the persisted Decimal price grammar no regex states (shared
grammar). Each line gives the check, its owner and why. The first 40 are the
`mapper_cross_field` rows of `frontend/tests/decoderDifferential.test.ts`; X40
to X61 and the policy checks P-X1 to P-X4 are checks that suite does not
exercise.

- `check:mapActivity.failure-reasons-order`: failure reasons ascend.
  `_ProviderRecoveryActivity.failure_reasons`. Positional.
- `check:mapLibraryCover.preview-binding`: the cover url names its own
  `preview_sha256`. `_LibraryCover`. Cross-field.
- `check:mapAudiobookReviewScope.media-kind`: a review scope needs an audio
  media kind. `_ReportDetailResponse`. Cross-field.
- `check:mapAudiobookReviewScope.basis`: a recording basis needs the recording
  scope. `_ReportDetailResponse`. Cross-field.
- `check:mapDescriptionAdvice.outcome-needs-selection`: an application outcome
  needs a selection. `_DescriptionAdviceResponse`. Cross-field.
- `check:mapDescriptionAdvice.selection-pairing`: the selection and
  `selected_advice` are both present or both null.
  `_DescriptionAdviceResponse`. Cross-field.
- `check:mapDescriptionAdvice.selection-candidate`: the selection names the
  selected candidate. `_DescriptionAdviceResponse` and
  `DescriptionAdviceCandidate`. Cross-field.
- `check:mapDescriptionAdvice.selection-binding`: the selection binding equals
  the selected advice binding. `_DescriptionAdviceResponse` and
  `DescriptionAdviceBinding`. Cross-field.
- `check:mapChoiceContexts.current-first`: the first choice context is the
  current value. `_ReviewField.choice_contexts`. Positional.
- `check:preparationPresentation.passage-order`: each passage count is at most
  the one before it. `_PreparationPassage`. Cross-field.
- `check:preparationPresentation.trigger-families`: one entry per trigger
  family. `_PreparationPresentation.triggers`. Identity across items.
- `check:preparationPresentation.active-within-total`: active books plus
  overflow are at most the total. `_PreparationPresentation`. Cross-field.
- `check:preparationPresentation.updated-after-start`: `updated_at` is not
  before `started_at`. `_PreparationPresentation`. Cross-field.
- `check:preparationPresentation.completion-iff-terminal`: `completed_at` is
  set exactly for a terminal state. `_PreparationPresentation`. Cross-field.
- `check:preparationPresentation.completed-after-start`: `completed_at` is not
  before `started_at`. `_PreparationPresentation`. Cross-field.
- `check:reviewerStatus.codex-reason`: the codex state fixes which reasons it
  may carry. `ReviewerEngineStatus`. Cross-field (slot).
- `check:reviewerStatus.codex-model-presence`: the codex model is null exactly
  when not provisioned. `ReviewerEngineStatus`. Cross-field (slot).
- `check:reviewerStatus.codex-model`: a codex model matches the local model
  grammar. `ReviewerEngineStatus.model`, shared with the openrouter slot,
  whose `provider/model` values are valid. Cross-field (slot).
- `check:mapReviewerAccess.cover-model`: cover metadata prices the saved
  openrouter mapping model. `CoverModelMetadata`. Cross-field.
- `check:mapReviewerAccess.cover-price`: a cover price is a decimal in
  [0, 1000000]. `CoverModelMetadata`, stored in `ReviewerAccessDocument`.
  Persisted.
- `check:mapDescriptionMetadata.price`: a description price is a decimal in
  [0, 1000000]. `DescriptionModelMetadata`, stored in
  `ReviewerAccessDocument`. Persisted.
- `check:mapCoverConsideration.manual-eligibility`: `manual_eligible` follows
  the incumbent and the identity. `_CoverCandidate`. Cross-field.
- `check:mapCoverConsideration.rank-key-reference`: the rank key ends with the
  candidate reference. `_CoverCandidate`. Cross-field.
- `check:mapCoverConsideration.match-state`: `match_state` follows the
  identity outcome. `_CoverCandidate`. Cross-field.
- `check:mapCoverConsideration.unconfirmed-unranked`: an unconfirmed candidate
  is neither selectable nor ranked. `_CoverCandidate`. Cross-field.
- `check:mapCoverConsideration.match-reason`: a matched-book reason is carried
  only by a proposed candidate whose identity is not a conflict.
  `_CoverCandidate`. Cross-field.
- `check:mapCoverObservation.binding`: an observation names its candidate
  reference and preview. `_CoverObservation`. Cross-field.
- `check:mapCoverConsideration.unique-references`: candidate references are
  distinct. `_CoverConsiderationResponse.candidates`. Identity across items.
- `check:mapCoverConsideration.ordinal-position`: a candidate's ordinal is its
  position. `_CoverCandidate.ordinal`. Positional.
- `check:mapCoverConsideration.absent-incumbent`: an absent or unavailable
  incumbent has no reference and no incumbent candidate. `_CoverIncumbent`.
  Cross-field.
- `check:mapCoverConsideration.available-incumbent`: an available incumbent is
  the one incumbent candidate. `_CoverIncumbent`. Cross-field.
- `check:mapChoiceContexts.proposed-count`: one proposed context per manually
  eligible alternative. `_CoverConsiderationResponse.choice_contexts`.
  Cross-field.
- `check:mapCoverConsideration.policy-presence`: a policy report exists
  exactly when there are candidates. `_CoverConsiderationResponse`.
  Cross-field.
- `check:mapCoverConsideration.reviewer-failure-status`: a reviewer failure
  code needs the failed status. `_CoverConsiderationResponse`. Cross-field.
- `check:mapCoverConsideration.recommended-reference`: a recommended policy
  names its candidate. `_CoverPolicyReport`. Cross-field.
- `check:mapCoverConsideration.acceptable-ranking`: acceptable references are
  the ranked alternatives in rank order. `_CoverPolicyReport`. Cross-field.
- `check:mapCoverConsideration.derived-candidate-state`: a candidate state
  follows selectability, the recommendation and the acceptable list.
  `_CoverCandidate`. Cross-field.
- `check:mapCoverConsideration.recommendation-context`: only the recommended
  candidate's context is recommended. `_ChoiceContext`. Cross-field.
- `check:mapCoverConsideration.readiness`: `ready` follows
  `provider_fetching` and the candidates. `_CoverConsiderationResponse`.
  Cross-field.
- `check:mapDecisionAcknowledgement.case-identity`: the confirmed case is the
  requested case. `_ConfirmedDecision`. Identity across documents.
- `capture:post-reviewer-access-codex-device`: issued and waiting need the
  url, code and 900-second expiry, and every other state has none.
  `_CodexDeviceAuthResponse`. Cross-field (#1652 makes the capture realistic).
- X40: the engine equals its slot. `ReviewerEngineStatus.engine`. Cross-field
  (slot).
- X41: the codex slot's state is in the codex subset.
  `ReviewerEngineStatus.state`. Cross-field (slot).
- X42: the codex tier is null, and the openrouter headroom is null.
  `ReviewerEngineStatus`. Cross-field (slot).
- X43: the per-slot couplings of state, model, tier and reason, including the
  openrouter model grammar on the shared model field. `ReviewerEngineStatus`.
  Cross-field.
- X44: cost-evidence accounting (selected, attempted and completed runs, cost
  and work sums). `ReviewerCostEstimate`. Cross-field.
- X45: the cost-evidence price grammar. `ReviewerCostEstimate`. Shared
  grammar (the persisted `bounded_price` Decimal).
- X46: model and task pairs are unique. `ReviewerCostEvidence.models`.
  Identity across items.
- X47: the device state couples url, code and expiry.
  `_CodexDeviceAuthResponse`. Cross-field.
- X48: a trigger's `last_accepted_at` is not before its `first_accepted_at`.
  `_PreparationTrigger`. Cross-field.
- X49: description candidate text is not blank.
  `DescriptionAdviceCandidate.output.description`. Persisted.
- X50: the candidate and advice bind the same attempt, engine and model.
  `_DescriptionAdviceResponse` and its candidate. Cross-field.
- X51: a selection's `decision_id` prefix matches its origin.
  `_DescriptionSelection`. Cross-field.
- X52: the rank key's first six items are integers and the last is the
  reference. `_CoverCandidate.deterministic_rank_key`. Positional; only its
  length of 7 is stated.
- X53: a cover history decision is accept, keep_current, reject or candidate.
  `_HistoryCoverItem.decision`, the shared `_DecisionProjection.outcome`,
  which admits five. Cross-field (a subset that depends on the item kind).
- X54: each passage count is at most the run's `total`.
  `_PreparationPresentation.passage`. Cross-field.
- X55: the current choice context names no alternative.
  `_ChoiceContext.alternative_id` on the first context. Positional.
- X56: every later choice context is proposed and names an alternative.
  `_ChoiceContext.selection` and `alternative_id`. Positional.
- X57: proposed alternatives are distinct. `_ReviewField.choice_contexts`
  and `_CoverConsiderationResponse.choice_contexts`. Identity across items.
- X58: candidate artifacts are distinct.
  `_CoverConsiderationResponse.candidates`. Identity across items.
- X59: a confirmed incumbent is unselectable and unranked but has a rank key,
  and a confirmed alternative has a rank key. `_CoverCandidate`. Cross-field.
- X60: proposed cover contexts follow the manually eligible alternatives in
  candidate order. `_CoverConsiderationResponse.choice_contexts`. Positional.
- X61: an unavailable reason needs the `unavailable` status.
  `_CoverConsiderationResponse.reviewer_unavailable_reason`. Cross-field.
- P-X1: a preview library has one comparison per sampled work.
  `_PolicyImpactResponse.comparisons` and `cases`. Cross-field.
- P-X2: both sides of a field comparison describe the same field.
  `_PolicyFieldComparisonResponse.active` and `draft`. Cross-field.
- P-X3: an activation answers for the policy the request named.
  `_PolicyActivationResponse.policy_id` and the request's `policy_id`.
  Identity across documents. A failure is an uncertain write.
- P-X4: a preview is of the snapshot just saved.
  `_PolicyPreviewResponse.policy_id` and `_PolicyDraftResponse.policy_id`.
  Identity across documents; the Policy page makes it, because it compares two
  answers.

## Alternatives rejected

- A schema library at runtime, or a table interpreter: both add a dependency
  or a second schema language for what the OpenAPI document already states.
- Keeping the hand-written mappers as the response check: drift stays
  invisible until a field is read.
- The identity on every response: an unauthenticated caller could then read
  the installed version.
- Per-caller error branches without a shared predicate: they are easy to miss.

## Consequences

### Positive

- An old tab that is still signed in sees the reload notice on its next
  request; one whose session has expired goes to sign-in, which serves the new
  shell.
- A failure after a committed write reads as "could not be confirmed" with
  the current state reloaded, instead of a false refusal.

### Negative

- A provider-derived validation failure before the OpenRouter key is saved is
  now answered 500 and read as uncertain, though nothing was saved. This is
  the conservative direction.
- Every authenticated read now sends one more header.

### Neutral or deferred

- Since #1684 every response family the browser reads is decoded, the
  hand-written checks the schema states are removed, and ADR-0043's
  HTTP-module text and drift check are amended. Since #1728 every JSON answer
  is decoded with no per-operation gate, every policy command is typed by its
  operation, and each read or command takes its answer's type from the
  operation its path names.
