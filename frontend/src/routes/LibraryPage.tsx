import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, SyntheticEvent } from 'react'

import { displayValue } from '../api/http'
import type {
  CaseDetailView,
  ChoiceContextView,
  DecisionCommand,
  LibraryView,
  LibraryWorkView,
  ProviderOutcomeView,
} from '../api/http'
import { useCoverChoiceData } from '../coverChoices'
import { useRouteReadyFocus } from '../focus'
import { fieldLabel, labelWords, opaqueSource } from '../labels'
import { coalesceMilliseconds } from '../liveInvalidation'
import { detailQuery, libraryRoute, PreparationNotice, useReviewContext } from '../router'
import { WriteReadyNotice } from '../components/WriteReadyNotice'
import { actionable, requestKey, useReviewDecisionController } from '../reviewDecisionController'
import { automaticChoiceSelected, humanChoiceSelected } from '../reviewAutomaticSelection'
import { ownerChoiceCount } from '../reviewChoiceCounts'

type ReviewField = CaseDetailView['fields'][number]
const searchNavigationDelay = 150
// Live Library invalidation batches the documented fifty-work burst for 120 ms.
// Keep each distinct subject from that bounded batch long enough to compare it
// with the next bounded page read, but never treat a frame as selection
// authority.
const maxCoalescedSuccessorCandidates = 50
const librarySearchIdentity = (search: object) =>
  JSON.stringify(Object.entries(search).sort(([left], [right]) => left.localeCompare(right)))
// What the owner has chosen for one field and how far that choice has got.
// `null` means this field carries no choice of theirs in this session.
type ChoiceProgress = {
  command: DecisionCommand
  phase: 'recording' | 'confirmed' | 'failed'
} | null

const authorLine = (work: LibraryWorkView) =>
  work.authors.length ? work.authors.join(', ') : 'Author details unavailable'
// The server's closed lane status is an audit token, not something an owner can
// read: `candidates` and `candidate_limit_exceeded` differ by one word and mean
// opposite things. The consequence beside it is the server's own owner-facing
// sentence; this names the outcome the owner is looking for.
// The static map is exercised directly by the source-status content proof.
// eslint-disable-next-line react-refresh/only-export-components
export const sourceStatusLabel = (status: string) =>
  status === 'candidates'
    ? 'Match'
    : status === 'no_match'
      ? 'No match'
      : status === 'candidate_limit_exceeded'
        ? 'Too many candidates'
        : status === 'rate_limited' || status === 'unavailable'
          ? 'Retry scheduled'
          : status === 'disabled'
            ? 'Not enabled'
            : status === 'insufficient_input'
              ? 'Not enough identity'
              : status === 'query_limit_exceeded'
                ? 'Query allowance reached'
                : 'Provider failure'
const initials = (title: string) =>
  title
    .split(/\s+/u)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join('') || 'ML'
const bounded = (value: string, fallback: string) =>
  opaqueSource.test(value) ? fallback : value.length > 160 ? `${value.slice(0, 157)}…` : value
const decisionValue = (field: ReviewField) => {
  const selected = field.humanDecision?.selected_value ?? field.automaticDecision?.selected_value
  return selected === null || selected === undefined
    ? field.currentValue
    : displayValue(selected.value)
}
const fieldState = (field: ReviewField) =>
  field.humanDecision
    ? 'Owner decision recorded'
    : field.automaticDecision
      ? 'Resolved automatically'
      : actionable(field)
        ? 'Needs review'
        : 'No eligible change'
const fieldReason = (field: ReviewField) =>
  bounded(
    field.humanDecision
      ? field.classificationReason ||
          `${fieldLabel(field.humanDecision.outcome)} recorded for this field.`
      : field.automaticDecision
        ? field.classificationReason ||
          `${fieldLabel(field.automaticDecision.outcome)} selected by deterministic policy.`
        : actionable(field)
          ? field.classificationReason ||
            `${fieldLabel(field.disposition)} requires an owner comparison of the retained values.`
          : field.classificationReason &&
              !/needs (?:your )?review/iu.test(field.classificationReason)
            ? field.classificationReason
            : 'No eligible owner decision is available.',
    'Retained reviewer notice',
  )
const effectSummary = (field: ReviewField) =>
  field.effectCapability === 'allowed'
    ? {
        state: 'Eligible for guarded planning',
        detail:
          'This decision queues nothing. Backup, currentness, validation and read-back checks remain separate gates.',
      }
    : field.effectCapability === 'unavailable'
      ? {
          state: 'External update unavailable',
          detail:
            'The decision can still be recorded; no external update capability is configured.',
        }
      : {
          state: 'Guarded from external update',
          detail: `${fieldLabel(field.effectCapability)} currently prevents an external update.`,
        }
const completionOutcome = (field: ReviewField) =>
  field.humanDecision?.outcome === 'reject'
    ? 'No action recorded'
    : field.humanDecision?.outcome === 'keep_current'
      ? 'Current value retained'
      : field.humanDecision?.outcome === 'accept'
        ? 'Proposed value selected'
        : 'Recorded outcome'
const sameSelectedValue = (
  left: DecisionCommand['selectedValue'],
  right: DecisionCommand['selectedValue'],
) =>
  left == null || right == null
    ? left === right
    : left.kind === right.kind && JSON.stringify(left.value) === JSON.stringify(right.value)
const commandSelected = (
  command: DecisionCommand | null,
  destination: string,
  outcome: DecisionCommand['outcome'],
  selectedValue: DecisionCommand['selectedValue'] = null,
) =>
  command?.destination === destination &&
  command.outcome === outcome &&
  sameSelectedValue(command.selectedValue, selectedValue)
// A recorded field is revisited with every option still shown, so which one
// is the recorded answer has to be readable. The owner's own decision is the
// answer whenever there is one; an automatic decision only speaks for a field
// they have not decided themselves.
const recordedSelected = (
  field: ReviewField,
  outcome: DecisionCommand['outcome'],
  selectedValue: DecisionCommand['selectedValue'] = null,
) =>
  field.humanDecision !== null
    ? humanChoiceSelected(field.humanDecision, outcome, selectedValue)
    : field.automaticDecision !== null &&
      automaticChoiceSelected(field.automaticDecision, outcome, selectedValue)
const choiceSelected = (
  field: ReviewField,
  progress: ChoiceProgress,
  outcome: DecisionCommand['outcome'],
  selectedValue: DecisionCommand['selectedValue'] = null,
) =>
  (progress?.phase !== 'failed' &&
    commandSelected(progress?.command ?? null, field.destination, outcome, selectedValue)) ||
  ((progress === null || progress.phase === 'failed') &&
    recordedSelected(field, outcome, selectedValue))
const choiceContext = (field: ReviewField, alternativeId: string | null) =>
  field.choiceContexts.find((context) => context.alternativeId === alternativeId)
const materialComparisonReasons = new Set([
  'bibliographic_combination_unresolved',
  'competing_evidence',
  'date_refinement_requires_corroboration',
  'date_year_conflict',
  'field_policy_conflict',
  'insufficient_independent_corroboration',
  'no_safe_bibliographic_combination',
  'populated_subjective_field',
])
const comparisonNotices = (field: ReviewField) =>
  field.choiceContexts.some((context) =>
    context.limitations.some((limitation) => materialComparisonReasons.has(limitation)),
  )
    ? []
    : field.notices.filter((notice) => materialComparisonReasons.has(notice))

// Two surfaces, one disclosure. The review comparison carries the protected
// evidence footer -- one line, no subtitle -- while the work summary, which is
// not a comparison, keeps naming what it consulted.
function WorkSources({
  outcomes,
  comparison = false,
}: {
  outcomes: readonly ProviderOutcomeView[]
  comparison?: boolean
}) {
  if (!outcomes.length) return null
  return (
    <details className="work-sources">
      <summary>
        {comparison ? (
          <span>
            {`${String(outcomes.length)} ${outcomes.length === 1 ? 'source supports' : 'sources support'} this comparison`}
          </span>
        ) : (
          <>
            <span>Sources</span>
            <small>{String(outcomes.length)} consulted for this work</small>
          </>
        )}
      </summary>
      <ul>
        {outcomes.map((outcome, index) => (
          <li key={`${outcome.provider}:${outcome.laneKind}:${outcome.status}:${String(index)}`}>
            <strong>{outcome.provider}</strong>
            <b>{sourceStatusLabel(outcome.status)}</b>
            <span>{outcome.reason}</span>
            <small>
              {fieldLabel(outcome.laneKind)} lookup · {String(outcome.providerRequests)} bounded
              request{outcome.providerRequests === 1 ? '' : 's'}
            </small>
          </li>
        ))}
      </ul>
    </details>
  )
}

function WorkSummary({ work }: { work: LibraryWorkView }) {
  const summary = work.summary
  return (
    <>
      <header className="selected-work-header" tabIndex={-1} data-route-focus="true">
        <span className="library-book-mark" aria-hidden="true">
          {initials(work.title)}
        </span>
        <div>
          <h2>{work.title}</h2>
          <p>
            {authorLine(work)} · {work.library} · {labelWords(work.mediaKind)}
          </p>
        </div>
      </header>
      <p className="library-work-summary-reason" role="status">
        {summary?.reason ?? 'No review decision is recorded for this work.'}
      </p>
      {work.preparation ? (
        <aside className="comparison-notice" aria-label="Preparation condition">
          <p>
            <strong>
              {fieldLabel(work.preparation.status)} at {fieldLabel(work.preparation.stage)}
            </strong>
          </p>
          <p>
            {work.preparation.reason} {work.preparation.recovery}
          </p>
        </aside>
      ) : null}
      {summary?.fields.length ? (
        <dl className="library-work-values">
          {summary.fields.map((field) => (
            <div key={field.destination}>
              <dt>{fieldLabel(field.destination)}</dt>
              <dd>{field.state === 'value' ? field.value : fieldLabel(field.state)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <WorkSources outcomes={work.preparationOutcomes} />
    </>
  )
}

function ChoiceEvidencePanel({
  context,
  evidenceId,
  label,
  onChoose,
}: {
  context: ChoiceContextView | undefined
  evidenceId: string
  label: string
  onChoose?: () => void
}) {
  return (
    <section
      className="choice-evidence"
      id={evidenceId}
      aria-label={`${label} evidence`}
      {...(onChoose
        ? {
            role: 'button' as const,
            tabIndex: 0,
            onClick: onChoose,
            onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onChoose()
              }
            },
          }
        : {})}
    >
      <p>{context?.rationale ?? 'Retained evidence is unavailable for this choice.'}</p>
      {context?.evidence.length ? (
        <ul>
          {context.evidence.map((row, index) => (
            <li key={`${row.source}:${row.observation}:${String(index)}`}>
              <strong>{row.source}</strong>
              <span>
                {row.role === 'local_presence' ? 'Local presence' : 'External corroboration'}
              </span>
              <p>{row.observation}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p>No owner-readable source observation is retained for this choice.</p>
      )}
      {context?.limitations.length ? (
        <aside>
          <strong>Limitation</strong>
          {context.limitations.map((limitation) => (
            <p key={limitation}>{fieldLabel(limitation)}</p>
          ))}
        </aside>
      ) : null}
    </section>
  )
}

const isLongDescription = (destination: string, value: string) =>
  destination === 'description' && value.length > 280

const decisionQuestion = (destination: string) =>
  `Which ${labelWords(destination)} should this work use?`

function MetadataChoice({
  context,
  current,
  disabled,
  expanded,
  label,
  longDescription,
  onChoose,
  recording,
  selected,
  toggle,
  value,
}: {
  context: ChoiceContextView | undefined
  current: boolean
  disabled: boolean
  expanded: boolean
  label: string
  longDescription: boolean
  onChoose: () => void
  recording: boolean
  selected: boolean
  toggle: () => void
  value: string
}) {
  const evidenceId = `choice-evidence-${current ? 'current' : (context?.alternativeId ?? 'alternative')}`
  const evidenceLabel = `Evidence for ${label}: ${value}`
  const hasEvidence = Boolean(context && (context.evidence.length || context.limitations.length))
  return (
    <article
      className={`metadata-choice${current ? ' is-current' : ''}${context?.recommendation === 'recommended' ? ' is-recommended' : ''}${selected ? ' is-selected' : ''}${expanded ? ' is-evidence-open' : ''}`}
    >
      <button
        type="button"
        className="value-choice choice-select"
        aria-label={`Choose ${current ? 'current' : 'proposed'} value: ${value}`}
        aria-pressed={selected}
        disabled={disabled}
        onClick={onChoose}
      >
        <small>
          {label}
          {context?.recommendation === 'recommended' ? ' · Recommended' : ''}
        </small>
        <strong>{longDescription ? `${value.slice(0, 240)}…` : value}</strong>
        <span>{context?.rationale ?? 'Retained for this owner comparison.'}</span>
        <span className="choice-recommendation">
          {recording
            ? 'Recording…'
            : context?.recommendation === 'recommended'
              ? context.recommendationReason
              : 'No recommendation retained'}
        </span>
      </button>
      {longDescription ? (
        <details className="choice-description">
          <summary>Read full description</summary>
          <p
            aria-label={`Choose ${current ? 'current' : 'proposed'} full description`}
            className="choice-description-value"
            role="button"
            tabIndex={0}
            onClick={onChoose}
            onKeyDown={(event) => {
              if (event.key !== 'Enter' && event.key !== ' ') return
              event.preventDefault()
              onChoose()
            }}
          >
            {value}
          </p>
        </details>
      ) : null}
      {hasEvidence ? (
        <button
          type="button"
          className="choice-evidence-toggle"
          aria-controls={evidenceId}
          aria-expanded={expanded}
          aria-label={evidenceLabel}
          onClick={toggle}
        >
          {expanded ? 'Hide evidence' : 'Evidence'}
        </button>
      ) : null}
      {expanded ? (
        <ChoiceEvidencePanel
          context={context}
          evidenceId={evidenceId}
          label={label}
          onChoose={onChoose}
        />
      ) : null}
    </article>
  )
}

function ContextValue({
  field,
  expanded,
  toggle,
}: {
  field: ReviewField
  expanded: boolean
  toggle: () => void
}) {
  const context = choiceContext(field, null)
  const evidenceId = `context-evidence-${field.destination}`
  return (
    <section
      className="context-value"
      aria-label={`${fieldLabel(field.destination)} current value`}
    >
      <dl>
        <div>
          <dt>Current value</dt>
          <dd>{displayValue(field.currentValue)}</dd>
        </div>
      </dl>
      <button
        type="button"
        className="context-evidence-toggle"
        aria-controls={evidenceId}
        aria-expanded={expanded}
        onClick={toggle}
      >
        {expanded ? 'Hide evidence' : 'Evidence'}
      </button>
      {expanded ? (
        <ChoiceEvidencePanel
          context={context}
          evidenceId={evidenceId}
          label={`${fieldLabel(field.destination)} current value`}
        />
      ) : null}
    </section>
  )
}

function RecordedOutcome({
  field,
  expanded,
  toggle,
}: {
  field: ReviewField
  expanded: boolean
  toggle: () => void
}) {
  const decision = field.humanDecision ?? field.automaticDecision
  if (!decision) return null
  const proposed = field.proposedValues.findIndex((value) =>
    sameSelectedValue(value, decision.selected_value),
  )
  const context =
    decision.outcome === 'accept' && proposed >= 0
      ? choiceContext(
          field,
          field.choiceContexts.filter((item) => item.selection === 'proposed')[proposed]
            ?.alternativeId ?? null,
        )
      : choiceContext(field, null)
  const evidenceId = `recorded-evidence-${field.destination}`
  const accepted = decision.outcome === 'accept'
  return (
    <section
      className="recorded-outcome"
      aria-label={`${fieldLabel(field.destination)} recorded outcome`}
    >
      <p>
        <strong>
          {field.automaticDecision ? 'Resolved automatically' : completionOutcome(field)}
        </strong>
      </p>
      <dl>
        {accepted ? (
          <div>
            <dt>Chosen value</dt>
            <dd>{decisionValue(field)}</dd>
          </div>
        ) : (
          <div>
            <dt>{decision.outcome === 'keep_current' ? 'Retained value' : 'Current value'}</dt>
            <dd>{decisionValue(field)}</dd>
          </div>
        )}
      </dl>
      <button
        type="button"
        className="context-evidence-toggle"
        aria-controls={evidenceId}
        aria-expanded={expanded}
        onClick={toggle}
      >
        {expanded ? 'Hide evidence' : 'Evidence'}
      </button>
      {expanded ? (
        <ChoiceEvidencePanel
          context={context}
          evidenceId={evidenceId}
          label={`${fieldLabel(field.destination)} recorded outcome`}
        />
      ) : null}
    </section>
  )
}

function LibraryCoverChoices(props: {
  reviewCaseId: string
  field: ReviewField
  disabled: boolean
  progress: ChoiceProgress
  expandedEvidence: { has: (key: string) => boolean }
  revealEvidence: (key: string) => void
  choose: (
    outcome: DecisionCommand['outcome'],
    selectedValue?: DecisionCommand['selectedValue'],
  ) => void
}) {
  return <LibraryCoverChoicesContent key={props.reviewCaseId} {...props} />
}

function LibraryCoverChoicesContent({
  reviewCaseId,
  field,
  disabled,
  expandedEvidence,
  progress,
  revealEvidence,
  choose,
}: {
  reviewCaseId: string
  field: ReviewField
  disabled: boolean
  progress: ChoiceProgress
  expandedEvidence: { has: (key: string) => boolean }
  revealEvidence: (key: string) => void
  choose: (
    outcome: DecisionCommand['outcome'],
    selectedValue?: DecisionCommand['selectedValue'],
  ) => void
}) {
  const cover = useCoverChoiceData(reviewCaseId)
  const consideration = field.coverConsideration
  const keeps = field.decisionActions.includes('keep_current')
  const accepts = field.decisionActions.includes('accept')
  if (!consideration)
    return (
      <section aria-label="Prepare cover comparison">
        <p role="status">
          No current cover comparison is available. The existing cover remains unchanged.
        </p>
        {keeps ? (
          <button type="button" disabled={disabled} onClick={() => choose('keep_current')}>
            Keep current cover
          </button>
        ) : null}
        <button
          type="button"
          disabled={disabled || cover.prepare.isPending}
          onClick={() => cover.prepare.mutate()}
        >
          Prepare cover comparison
        </button>
        {cover.prepare.isError ? (
          <p role="alert">
            The comparison could not be prepared. The existing cover remains unchanged.
          </p>
        ) : null}
      </section>
    )
  const candidates = consideration.candidates.filter(
    (candidate) => candidate.selectable || candidate.incumbent,
  )
  const excluded = consideration.candidates.filter(
    (candidate) => !candidate.selectable && !candidate.incumbent,
  )
  const expanded = candidates.length > 3
  const incumbent = candidates.find((candidate) => candidate.incumbent)
  const alternatives = candidates.filter((candidate) => !candidate.incumbent)
  const coverContexts = consideration.choiceContexts
  const currentContext = coverContexts[0]
  const acceptableReferences =
    Array.isArray(consideration.policy?.acceptable_candidate_references) &&
    consideration.policy.acceptable_candidate_references.every(
      (reference): reference is string => typeof reference === 'string',
    )
      ? consideration.policy.acceptable_candidate_references
      : []
  const policyStatus =
    typeof consideration.policy?.status === 'string' ? consideration.policy.status : null
  const policyReasons = Array.isArray(consideration.policy?.reasons)
    ? consideration.policy.reasons
        .filter((reason): reason is string => typeof reason === 'string')
        .slice(0, 2)
    : []
  const excludedReason = (candidate: (typeof candidates)[number]) =>
    consideration.policyPreferences.avoidPromotionalText &&
    candidate.observation?.promotional_text === 'present'
      ? 'Promotional text excluded by policy'
      : consideration.policyPreferences.avoidTieInBranding &&
          candidate.observation?.tie_in_branding === 'present'
        ? 'Tie in branding excluded by policy'
        : typeof candidate.identity.outcome === 'string' &&
            !['plausible_match', 'verified_work_match', 'verified_manifestation_match'].includes(
              candidate.identity.outcome,
            )
          ? `${fieldLabel(candidate.identity.outcome)}${
              Array.isArray(candidate.identity.reasons)
                ? ` · ${candidate.identity.reasons
                    .filter((reason): reason is string => typeof reason === 'string')
                    .slice(0, 2)
                    .map(fieldLabel)
                    .join(', ')}`
                : ''
            }`
          : candidate.observation === null
            ? 'Visual observation unavailable'
            : policyStatus !== null && !acceptableReferences.includes(candidate.reference)
              ? policyReasons.length
                ? policyReasons.map(fieldLabel).join(', ')
                : fieldLabel(policyStatus)
              : fieldLabel(candidate.state)
  const card = (candidate: (typeof candidates)[number], index: number) => {
    const alternative = candidates.slice(0, index + 1).filter((item) => !item.incumbent).length
    const source = cover.imageSource(consideration.id, candidate.reference)
    const outcome = candidate.incumbent ? 'keep_current' : 'accept'
    const value: DecisionCommand['selectedValue'] = candidate.incumbent
      ? null
      : { kind: 'cover', value: candidate.artifactId }
    const selected = choiceSelected(field, progress, outcome, value)
    if ((candidate.incumbent && !keeps) || (!candidate.incumbent && !accepts)) return null
    const context = candidate.incumbent
      ? currentContext
      : coverContexts.find((item) => item.alternativeId === candidate.reference)
    const key = `${candidate.incumbent ? 'current' : 'alternative'}-${String(index)}`
    const evidenceId = `cover-evidence-${String(candidate.ordinal)}-${key}`
    const label = candidate.incumbent ? 'Current cover' : `Alternative ${String(alternative)} cover`
    const evidenceKey = `cover:${key}`
    const open = expandedEvidence.has(evidenceKey)
    return (
      <article
        className={`cover-choice${open ? ' is-evidence-open' : ''}`}
        key={candidate.reference}
      >
        <button
          type="button"
          aria-pressed={selected}
          className={`library-cover-card${candidate.incumbent ? ' is-current' : ''}${context?.recommendation === 'recommended' ? ' is-recommended' : ''}${selected ? ' is-selected' : ''}`}
          disabled={disabled}
          onClick={() => choose(outcome, value)}
        >
          <span className="library-cover-image">
            {cover.imageFailed(candidate.reference) ? (
              <span role="img" aria-label="Cover unavailable">
                Image unavailable
              </span>
            ) : (
              <img
                src={source}
                alt={label}
                onError={() => cover.markImageFailed(candidate.reference)}
              />
            )}
          </span>
          <span>
            <small>
              {label}
              {context?.recommendation === 'recommended' ? ' · Recommended' : ''}
            </small>
            <strong>{fieldLabel(candidate.state)}</strong>
            <em>
              {context?.rationale ??
                `${String(candidate.width)} × ${String(candidate.height)} · ${candidate.sourceKinds.map(fieldLabel).join(', ')}`}
            </em>
            <span className="choice-recommendation">
              {context?.recommendation === 'recommended'
                ? context.recommendationReason
                : 'No recommendation retained'}
            </span>
            {selected && progress?.phase === 'recording' ? <b>Recording…</b> : null}
          </span>
        </button>
        {context && (context.evidence.length || context.limitations.length) ? (
          <button
            type="button"
            className="choice-evidence-toggle"
            aria-controls={evidenceId}
            aria-expanded={open}
            aria-label={`Evidence for ${label}`}
            onClick={() => revealEvidence(evidenceKey)}
          >
            {open ? 'Hide evidence' : 'Evidence'}
          </button>
        ) : null}
        {open ? (
          <ChoiceEvidencePanel
            context={context}
            evidenceId={evidenceId}
            label={label}
            {...(disabled ? {} : { onChoose: () => choose(outcome, value) })}
          />
        ) : null}
      </article>
    )
  }
  const reviewerUnavailable =
    consideration.reviewerStatus === 'failed' || consideration.reviewerStatus === 'unavailable'
  const reviewerLabel =
    consideration.reviewerStatus === 'completed'
      ? 'Advisory visual review complete'
      : reviewerUnavailable
        ? 'Manual comparison available'
        : 'Manual cover comparison'
  return (
    <section
      className={`library-cover-comparison${expanded ? ' is-expanded' : ''}`}
      aria-label="Choose a cover"
    >
      <header>
        <strong>{reviewerLabel}</strong>
        <small>
          {coverContexts.some((context) => context.recommendation === 'recommended')
            ? 'One candidate is recommended as advice only.'
            : 'Recommendation is unavailable; comparison remains manual.'}
        </small>
      </header>
      <p className="cover-track-hint">
        Every cover is shown whole in one row. Scroll the row, or tab between covers, to compare
        them.
      </p>
      {reviewerUnavailable ? (
        <button
          className="secondary-action"
          type="button"
          disabled={disabled || cover.prepare.isPending}
          onClick={() => cover.prepare.mutate()}
        >
          Retry advisory review
        </button>
      ) : null}
      {cover.prepare.isError ? (
        <p role="alert">
          The advisory retry failed safely. The existing cover and manual choices are unchanged.
        </p>
      ) : null}
      {expanded && incumbent ? (
        <div className="library-cover-pinned">
          <aside>
            <strong>Current reference</strong>
            {card(incumbent, candidates.indexOf(incumbent))}
          </aside>
          <div className="library-cover-gallery">
            <header>
              <strong>Eligible alternatives</strong>
              <small>{String(alternatives.length)} available</small>
            </header>
            <div>
              {alternatives.map((candidate) => card(candidate, candidates.indexOf(candidate)))}
            </div>
          </div>
        </div>
      ) : (
        <div className="library-cover-layout">{candidates.map(card)}</div>
      )}
      {consideration.incumbent.state === 'unavailable' && !incumbent && keeps ? (
        <article className="cover-choice">
          <button
            type="button"
            aria-pressed={choiceSelected(field, progress, 'keep_current')}
            className={`library-cover-card is-current${choiceSelected(field, progress, 'keep_current') ? ' is-selected' : ''}`}
            disabled={disabled}
            onClick={() => choose('keep_current')}
          >
            <span className="library-cover-image" role="img" aria-label="Current cover unavailable">
              Image unavailable
            </span>
            <span>
              <small>Current cover</small>
              <strong>Unavailable</strong>
              <em>
                {currentContext?.rationale ?? 'Keep the current bound cover without a preview.'}
              </em>
              <span className="choice-recommendation">
                {currentContext?.recommendation === 'recommended'
                  ? currentContext.recommendationReason
                  : 'No recommendation retained'}
              </span>
              {commandSelected(progress?.command ?? null, field.destination, 'keep_current') &&
              progress?.phase === 'recording' ? (
                <b>Recording…</b>
              ) : null}
            </span>
          </button>
          {currentContext &&
          (currentContext.evidence.length || currentContext.limitations.length) ? (
            <button
              type="button"
              className="choice-evidence-toggle"
              aria-controls="cover-evidence-current-unavailable"
              aria-expanded={expandedEvidence.has('cover:current-unavailable')}
              aria-label="Evidence for Current cover"
              onClick={() => revealEvidence('cover:current-unavailable')}
            >
              {expandedEvidence.has('cover:current-unavailable') ? 'Hide evidence' : 'Evidence'}
            </button>
          ) : null}
          {expandedEvidence.has('cover:current-unavailable') ? (
            <ChoiceEvidencePanel
              context={currentContext}
              evidenceId="cover-evidence-current-unavailable"
              label="Current cover"
              {...(disabled ? {} : { onChoose: () => choose('keep_current') })}
            />
          ) : null}
        </article>
      ) : null}
      {excluded.length || consideration.omissions.length ? (
        <details className="library-cover-excluded">
          <summary>
            {String(excluded.length + consideration.omissions.length)} excluded or unavailable
            candidate{excluded.length + consideration.omissions.length === 1 ? '' : 's'}{' '}
            <small>Reasons retained</small>
          </summary>
          {excluded.map((candidate) => (
            <p key={candidate.reference}>{excludedReason(candidate)}</p>
          ))}
          {consideration.omissions.map((omission, index) => (
            <p key={String(index)}>
              {typeof omission.reason === 'string'
                ? fieldLabel(omission.reason)
                : 'Unavailable source retained in history'}
            </p>
          ))}
        </details>
      ) : null}
    </section>
  )
}

export default function LibraryPage({ data }: { data: LibraryView }) {
  const search = libraryRoute.useSearch()
  const navigate = useNavigate({ from: '/library' })
  const { http, updates } = useReviewContext()
  const currentSearch = useRef(search)
  const pendingSearchQuery = useRef<string | null>(null)
  const searchNavigation = useRef<Promise<void> | null>(null)
  const searchCommitTimer = useRef<number | null>(null)
  const searchGeneration = useRef(0)
  const observedSearchIdentity = useRef(librarySearchIdentity(search))
  const expectedSearchIdentity = useRef<string | null>(null)
  const retryButton = useRef<HTMLButtonElement>(null)
  const completionRegion = useRef<HTMLElement>(null)
  const activeFieldButton = useRef<HTMLButtonElement>(null)
  const fieldRail = useRef<HTMLElement>(null)
  const advanceFocus = useRef(false)
  const advanceScroll = useRef<number | null>(null)
  const advancePresentation = useRef<{
    destination: string
    source: string
    sourceRevision: string
    target: string
    resolve: () => void
  } | null>(null)
  const releaseAdvancePresentation = useCallback(() => {
    const presentation = advancePresentation.current
    if (!presentation) return
    advancePresentation.current = null
    presentation.resolve()
  }, [])
  const automaticTrigger = useRef<HTMLButtonElement>(null)
  const keepManualHandling = useRef<HTMLButtonElement>(null)
  const restoreAutomaticTrigger = useRef(false)
  const attentionRail = useRef<HTMLElement>(null)
  const workSelectionGeneration = useRef(0)
  const selectedIdentityRef = useRef<{
    id: string
    title: string
    authors: readonly string[]
    library: string
    mediaKind: string
  } | null>(null)
  const selectedTargetRef = useRef<{
    caseId: string
    connection: string
    library: string
    item: string
  } | null>(null)
  const selectedPreviewRef = useRef<string | undefined>(undefined)
  const selectedDetailRetry = useRef<string | null>(null)
  const selectedDetailRetryButton = useRef<HTMLButtonElement>(null)
  const pendingSuccessorRef = useRef(new Set<string>())
  const lineageGenerationRef = useRef(0)
  const [activeLineageGeneration, setActiveLineageGeneration] = useState<number | null>(null)
  const [lineageReconciliation, setLineageReconciliation] = useState<{
    predecessor: string
    selection: string
    generation: number
  } | null>(null)
  const [successorGeneration, setSuccessorGeneration] = useState(0)
  const [frozenFieldOrders, setFrozenFieldOrders] = useState<
    Readonly<Record<string, readonly string[]>>
  >({})
  const [complete, setComplete] = useState<{
    id: string
    fields: readonly ReviewField[]
    priorAttention: ReadonlySet<string>
    identity: { title: string; authors: readonly string[]; library: string; mediaKind: string }
  } | null>(null)
  const [completionInspection, setCompletionInspection] = useState<string | null>(null)
  const [automaticPanel, setAutomaticPanel] = useState<string | null>(null)
  const [automaticResults, setAutomaticResults] = useState<
    Readonly<Record<string, 'success' | 'error'>>
  >({})
  const [openedWork, setOpenedWork] = useState<LibraryWorkView | null>(null)
  const [expandedChoiceState, setExpandedChoiceState] = useState<{
    work: string
    keys: ReadonlySet<string>
  }>({ work: '', keys: new Set() })
  // The URL owns the query. This holds only the exact characters typed since the
  // last URL move, so a trailing space survives the round trip; the moment the
  // URL says something this echo does not, the URL wins — which is what makes
  // Back, Forward and a deep link reseed the box instead of fighting it.
  const [typedQuery, setTypedQuery] = useState({ echo: search.q ?? '', text: search.q ?? '' })
  const [searchPending, setSearchPending] = useState(false)
  const urlQuery = search.q ?? ''
  const searchIdentity = librarySearchIdentity(search)
  const locationRevision = useRouterState({
    select: (state) =>
      state.location.state.__TSR_key ?? state.location.state.key ?? state.location.href,
  })
  const queryText = searchPending || typedQuery.echo === urlQuery ? typedQuery.text : urlQuery
  const selectedReviewCaseId = search.preview ?? ''
  useEffect(() => {
    if (selectedPreviewRef.current !== search.preview) {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
    }
    selectedPreviewRef.current = search.preview
    pendingSuccessorRef.current.clear()
  }, [search.preview])
  const invalidateSearch = useCallback(() => {
    ++searchGeneration.current
    pendingSearchQuery.current = null
    expectedSearchIdentity.current = null
    if (searchCommitTimer.current !== null) {
      window.clearTimeout(searchCommitTimer.current)
      searchCommitTimer.current = null
    }
  }, [])
  useEffect(() => {
    if (observedSearchIdentity.current === searchIdentity) return
    observedSearchIdentity.current = searchIdentity
    if (expectedSearchIdentity.current === searchIdentity) {
      expectedSearchIdentity.current = null
      return
    }
    invalidateSearch()
    setSearchPending(false)
  }, [invalidateSearch, searchIdentity])
  useEffect(() => {
    return () => {
      invalidateSearch()
    }
  }, [invalidateSearch])
  // A search or lens may temporarily hide the open work's card. The row the
  // owner opened is kept so the summary does not vanish mid-inspection and
  // send them back to the list; clearing the selection releases it.
  const selectedWork = search.work
    ? (data.works.find((work) => work.workId === search.work) ??
      (openedWork?.workId === search.work ? openedWork : undefined))
    : undefined
  const unresolvableWork = Boolean(search.work) && selectedWork === undefined
  // URL-owned selection has to stay truthful. A ?work= that resolves to nothing
  // on the bounded page in hand, and was not opened in this session, asserts a
  // selection the view cannot render, so it is normalised out of the URL. The
  // entry is replaced rather than pushed, so Back still leaves for wherever the
  // owner arrived from instead of returning to the unresolvable link.
  useEffect(() => {
    if (!unresolvableWork) return
    void navigate({
      replace: true,
      search: (current) => {
        const next = { ...current }
        delete next.work
        return next
      },
    })
  }, [unresolvableWork, navigate])
  const hasSelection = Boolean(search.preview) || selectedWork !== undefined
  const selectedLibraryWork = search.preview
    ? data.works.find((work) => work.reviewCaseId === search.preview)
    : undefined
  const detail = useQuery({
    ...detailQuery(http, selectedReviewCaseId),
    enabled: Boolean(search.preview),
  })
  const activeLineageReconciliation =
    lineageReconciliation !== null &&
    lineageReconciliation.predecessor === search.preview &&
    lineageReconciliation.selection === search.preview &&
    activeLineageGeneration === lineageReconciliation.generation
  const lineageSuccessor = useQuery({
    queryKey: [
      'review-case-successor',
      lineageReconciliation?.predecessor ?? null,
      lineageReconciliation?.generation ?? null,
    ],
    queryFn: ({ signal }) =>
      http.getCaseSuccessor(lineageReconciliation?.predecessor ?? '', signal),
    enabled: activeLineageReconciliation,
    retry: (failureCount) => activeLineageReconciliation && failureCount < 1,
    retryDelay: coalesceMilliseconds,
    retryOnMount: false,
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  })
  const attention = useInfiniteQuery({
    queryKey: ['attention', 'workspace', search.attentionCursor ?? null],
    initialPageParam: search.attentionCursor,
    queryFn: ({ pageParam, signal }) =>
      http.getAttention(pageParam ? { cursor: pageParam } : {}, signal),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    staleTime: 15_000,
    retry: false,
  })
  const attentionItems = useMemo(
    () => attention.data?.pages.flatMap((page) => page.items) ?? [],
    [attention.data],
  )
  const selectedIdentity = useMemo(
    () =>
      search.preview
        ? (data.works.find((work) => work.reviewCaseId === search.preview) ??
          attentionItems.find((item) => item.id === search.preview) ??
          (detail.data
            ? {
                id: detail.data.id,
                title: detail.data.title,
                authors: detail.data.authors,
                library: detail.data.target.library,
                mediaKind: detail.data.target.mediaKind,
              }
            : undefined))
        : undefined,
    [attentionItems, data.works, detail.data, search.preview],
  )
  useEffect(() => {
    if (selectedIdentity && search.preview)
      selectedIdentityRef.current = {
        id: search.preview,
        title: selectedIdentity.title,
        authors: selectedIdentity.authors,
        library: selectedIdentity.library,
        mediaKind: selectedIdentity.mediaKind,
      }
  }, [search.preview, selectedIdentity])
  useEffect(() => {
    if (search.preview && detail.data?.id === search.preview) {
      selectedTargetRef.current = { caseId: search.preview, ...detail.data.target }
      if (pendingSuccessorRef.current.size) setSuccessorGeneration((generation) => generation + 1)
    }
  }, [detail.data, search.preview])
  useLayoutEffect(() => {
    const reconciliation = lineageReconciliation
    if (
      !lineageSuccessor.isSuccess ||
      reconciliation === null ||
      reconciliation.selection !== search.preview ||
      selectedPreviewRef.current !== reconciliation.selection ||
      activeLineageGeneration !== reconciliation.generation
    )
      return
    // A successful null is authoritative; only the query's single bounded
    // retry treats transport failure as pending reconciliation.
    queueMicrotask(() => {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
    })
    if (
      lineageSuccessor.data !== null &&
      pendingSuccessorRef.current.size < maxCoalescedSuccessorCandidates
    ) {
      pendingSuccessorRef.current.add(lineageSuccessor.data)
      setSuccessorGeneration((generation) => generation + 1)
    }
  }, [
    activeLineageGeneration,
    lineageReconciliation,
    lineageSuccessor.data,
    lineageSuccessor.isSuccess,
    search.preview,
  ])
  useLayoutEffect(() => {
    const reconciliation = lineageReconciliation
    if (
      !lineageSuccessor.isError ||
      lineageSuccessor.failureCount < 2 ||
      reconciliation === null ||
      reconciliation.selection !== search.preview ||
      activeLineageGeneration !== reconciliation.generation
    )
      return
    queueMicrotask(() => {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
    })
  }, [
    activeLineageGeneration,
    lineageReconciliation,
    lineageSuccessor.failureCount,
    lineageSuccessor.isError,
    search.preview,
  ])
  useEffect(() => {
    if (!updates || !search.preview) return
    return updates.subscribe((update) => {
      if (update.topic !== 'library') return
      if (update.subject === null) {
        const predecessor = search.preview
        if (!predecessor) return
        const generation = ++lineageGenerationRef.current
        setActiveLineageGeneration(generation)
        setLineageReconciliation({ predecessor, selection: predecessor, generation })
        return
      }
      if (update.subject === search.preview) return
      if (
        pendingSuccessorRef.current.size < maxCoalescedSuccessorCandidates &&
        !pendingSuccessorRef.current.has(update.subject)
      ) {
        pendingSuccessorRef.current.add(update.subject)
        // Query structural sharing can make the next bounded Library read
        // referentially identical after a no-match batch. Candidate arrival is
        // itself the reason to reconcile that read once more.
        setSuccessorGeneration((generation) => generation + 1)
      }
    })
  }, [http, search.preview, updates])
  useEffect(() => {
    const selectedTarget = selectedTargetRef.current
    const successorIds = [...pendingSuccessorRef.current]
    if (
      !selectedTarget ||
      !successorIds.length ||
      selectedTarget.caseId !== search.preview ||
      data.works.some((work) => work.reviewCaseId === search.preview)
    )
      return
    pendingSuccessorRef.current.clear()
    void (async () => {
      try {
        const predecessor = await http.getCaseDetail(selectedTarget.caseId)
        if (
          predecessor.current ||
          predecessor.target.connection !== selectedTarget.connection ||
          predecessor.target.library !== selectedTarget.library ||
          predecessor.target.item !== selectedTarget.item
        )
          return
      } catch {
        return
      }
      for (const successorId of successorIds) {
        try {
          const successor = await http.getCaseDetail(successorId)
          if (
            !successor.current ||
            successor.target.connection !== selectedTarget.connection ||
            successor.target.library !== selectedTarget.library ||
            successor.target.item !== selectedTarget.item
          )
            continue
          await navigate({
            replace: true,
            search: (current) =>
              current.preview === selectedTarget.caseId
                ? { ...current, preview: successor.id }
                : current,
          })
          return
        } catch {
          // A case can disappear again between the bounded page read and its
          // authoritative detail read. The remaining coalesced candidates may
          // still name its replacement.
        }
      }
    })()
  }, [data.works, http, navigate, search.preview, successorGeneration])
  const completionIdentity = complete?.id === selectedReviewCaseId ? complete.identity : undefined
  const displayedIdentity = selectedIdentity ?? completionIdentity
  const primaryField = (field: ReviewField) => (field.inventoryRole ?? 'primary') === 'primary'
  const visibleFields = detail.data?.fields.filter(primaryField) ?? []
  const selected =
    visibleFields.find((field) => field.destination === search.field) ??
    visibleFields.find(actionable) ??
    visibleFields[0]
  const currentActionableDestinations = visibleFields.map((field) => field.destination)
  const frozenDestinations =
    frozenFieldOrders[selectedReviewCaseId] ?? currentActionableDestinations
  const railFields = frozenDestinations
    .map((destination) => visibleFields.find((field) => field.destination === destination))
    .filter((field): field is ReviewField => field !== undefined)
  const attentionFields = railFields.filter(actionable)
  const contextualFields =
    detail.data?.fields.filter((field) => field.inventoryRole === 'contextual') ?? []
  const activeQueueIndex = selected
    ? attentionItems.findIndex(
        (item) => item.id === search.preview && item.destination === selected.destination,
      )
    : -1
  const nextWork = complete
    ? (attentionItems.find(
        (item) =>
          item.id !== selectedReviewCaseId &&
          !complete.priorAttention.has(`${item.id}:${item.destination}`),
      ) ?? null)
    : null
  const nextWorkError = attention.isError
  const controller = useReviewDecisionController({
    reviewCaseId: selectedReviewCaseId,
    fields: detail.data?.fields ?? [],
    active: (destination) =>
      currentSearch.current.preview === search.preview &&
      search.preview !== undefined &&
      (currentSearch.current.field === undefined || currentSearch.current.field === destination),
    onAdvance: async (field) => {
      const advancing = field !== selected?.destination
      advanceFocus.current = advancing
      advanceScroll.current = advancing ? window.scrollY : null
      const presented = advancing
        ? new Promise<void>((resolve) => {
            advancePresentation.current = {
              destination: field,
              source: searchIdentity,
              sourceRevision: locationRevision,
              target: librarySearchIdentity({ ...search, field }),
              resolve,
            }
          })
        : null
      const navigation = { committed: false }
      try {
        await navigate({
          resetScroll: false,
          search: (current) => {
            if (current.preview !== search.preview) return current
            navigation.committed = true
            return { ...current, field }
          },
        })
      } catch {
        advanceFocus.current = false
        advanceScroll.current = null
        releaseAdvancePresentation()
        return
      }
      if (!navigation.committed) {
        advanceFocus.current = false
        advanceScroll.current = null
        releaseAdvancePresentation()
        return
      }
      // The controller may start background aggregate convergence only after
      // this exact next field has committed to the DOM.
      if (presented) await presented
    },
    onComplete: (fields) => {
      const retained = selectedIdentityRef.current
      if (retained?.id === selectedReviewCaseId) {
        setCompletionInspection(null)
        setComplete({
          id: selectedReviewCaseId,
          fields: fields.filter((field) => field.humanDecision !== null),
          priorAttention: new Set(
            (activeQueueIndex < 0 ? [] : attentionItems.slice(0, activeQueueIndex + 1)).map(
              (item) => `${item.id}:${item.destination}`,
            ),
          ),
          identity: retained,
        })
      }
    },
    onStale: async () => {
      await navigate({ search: { lens: 'needs_attention' } })
    },
  })
  const selectedAutomaticKey = selected
    ? `${selectedReviewCaseId}:${selected.destination}:${selected.targetFingerprint}`
    : null
  const automaticPanelVisible = automaticPanel === selectedAutomaticKey && selected?.manualHold
  const automaticResult = selectedAutomaticKey
    ? (automaticResults[selectedAutomaticKey] ?? null)
    : null
  const automaticCompletedForSelected = automaticResult === 'success'
  const returnAutomatic = useMutation({
    mutationFn: ({ destination }: { destination: string; key: string }) =>
      http.returnToAutomatic(selectedReviewCaseId, destination),
    retry: false,
    onSuccess: (_value, request) => {
      setAutomaticResults((current) => ({ ...current, [request.key]: 'success' }))
    },
    onError: (_error, request) =>
      setAutomaticResults((current) => ({ ...current, [request.key]: 'error' })),
  })

  const routeIdentity = `${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.attentionCursor ?? ''}:${search.preview ?? ''}:${search.work ?? ''}:${search.field ?? ''}:${search.attention ?? ''}`
  useRouteReadyFocus(routeIdentity)
  const selectedDetailReady =
    Boolean(search.preview) && search.attention !== 'open' && !detail.isPending && !detail.isError
  useEffect(() => {
    selectedDetailRetry.current = null
  }, [search.preview])
  useEffect(() => {
    const selectedDetailSettled = selectedDetailReady || detail.isError
    if (!selectedDetailSettled) return
    const workspace = document.querySelector<HTMLElement>('#main-content .workspace-detail')
    const active = document.activeElement
    const restoreAfterRetry =
      selectedDetailRetry.current === search.preview && active === document.body
    if (workspace && active === workspace && selectedDetailReady)
      workspace.querySelector<HTMLElement>('[data-route-focus]')?.focus({ preventScroll: true })
    else if (restoreAfterRetry) {
      if (detail.isError) selectedDetailRetryButton.current?.focus({ preventScroll: true })
      else
        workspace?.querySelector<HTMLElement>('[data-route-focus]')?.focus({ preventScroll: true })
    }
    selectedDetailRetry.current = null
  }, [detail.isError, search.preview, selectedDetailReady])
  useEffect(() => {
    currentSearch.current = search
  }, [search])
  useEffect(() => {
    if (controller.canRetry) retryButton.current?.focus()
  }, [controller.canRetry])
  useEffect(() => {
    if (complete?.id === selectedReviewCaseId && completionInspection === null)
      completionRegion.current?.focus({ preventScroll: true })
  }, [complete, completionInspection, selectedReviewCaseId])
  useLayoutEffect(() => {
    const presentation = advancePresentation.current
    if (!presentation) return
    if (
      (searchIdentity === presentation.target &&
        presentation.destination === selected?.destination) ||
      locationRevision !== presentation.sourceRevision
    )
      releaseAdvancePresentation()
  }, [locationRevision, releaseAdvancePresentation, searchIdentity, selected?.destination])
  useEffect(() => releaseAdvancePresentation, [releaseAdvancePresentation])
  useEffect(() => {
    const rail = fieldRail.current
    const active = activeFieldButton.current
    if (!rail || !active) return
    const reducedMotion =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    // This is a horizontal rail adjustment, not a request to reposition the
    // document. `scrollIntoView` can pull the selected work back to the top
    // when the owner records a middle field.
    const railBounds = rail.getBoundingClientRect()
    const activeBounds = active.getBoundingClientRect()
    const left = Math.max(
      0,
      rail.scrollLeft +
        activeBounds.left -
        railBounds.left -
        (rail.clientWidth - activeBounds.width) / 2,
    )
    if (typeof rail.scrollTo === 'function')
      rail.scrollTo({ left, behavior: reducedMotion ? 'auto' : 'smooth' })
    if (!advanceFocus.current) return
    advanceFocus.current = false
    const action = document.querySelector<HTMLButtonElement>(
      '.decision-canvas button:not(:disabled)',
    )
    action?.focus({ preventScroll: true })
    if (!action) return
    const bounds = action.getBoundingClientRect()
    const masthead = document.querySelector<HTMLElement>('.masthead')
    const visibleTop = (masthead?.getBoundingClientRect().bottom ?? 0) + 8
    const reveal =
      bounds.top < visibleTop
        ? bounds.top - visibleTop
        : Math.max(0, bounds.bottom - window.innerHeight)
    if (reveal) window.scrollBy({ top: reveal })
  }, [selected?.destination])
  useLayoutEffect(() => {
    if (advanceScroll.current === null) return
    const top = advanceScroll.current
    advanceScroll.current = null
    window.scrollTo({ top })
  }, [selected?.destination])
  useEffect(() => {
    if (automaticPanelVisible) keepManualHandling.current?.focus()
  }, [automaticPanelVisible, automaticResult])
  useEffect(() => {
    if (automaticPanel !== null || !restoreAutomaticTrigger.current) return
    restoreAutomaticTrigger.current = false
    automaticTrigger.current?.focus()
  }, [automaticPanel])
  useEffect(() => {
    if (search.attention !== 'open') return
    let focusFrame = 0
    const routeFrame = requestAnimationFrame(() => {
      focusFrame = requestAnimationFrame(() =>
        attentionRail.current?.focus({ preventScroll: true }),
      )
    })
    return () => {
      cancelAnimationFrame(routeFrame)
      cancelAnimationFrame(focusFrame)
    }
  }, [search.attention])
  useEffect(() => {
    if (
      complete?.id === selectedReviewCaseId &&
      !nextWork &&
      attention.hasNextPage &&
      !attention.isFetchingNextPage &&
      !attention.isFetchNextPageError
    )
      void attention.fetchNextPage()
  }, [attention, complete, nextWork, selectedReviewCaseId])
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(min-width: 761px)')
    const selectWideDefault = () => {
      if (!media.matches || currentSearch.current.preview) return
      const first =
        data.works.find((work) => work.reviewCaseId && work.attentionCount > 0) ??
        data.works.find((work) => work.reviewCaseId)
      const reviewCaseId = first?.reviewCaseId
      if (reviewCaseId)
        void navigate({
          replace: true,
          search: (current) => (current.preview ? current : { ...current, preview: reviewCaseId }),
        })
    }
    selectWideDefault()
    media.addEventListener('change', selectWideDefault)
    return () => media.removeEventListener('change', selectWideDefault)
  }, [data.works, navigate])

  const selectCase = (reviewCaseId: string, field?: string) => {
    workSelectionGeneration.current += 1
    if (reviewCaseId !== selectedReviewCaseId)
      setExpandedChoiceState({ work: reviewCaseId, keys: new Set() })
    if (complete?.id !== reviewCaseId) {
      setComplete(null)
      setCompletionInspection(null)
    }
    setOpenedWork(null)
    void navigate({
      search: (current) => {
        const next = { ...current, preview: reviewCaseId }
        delete next.work
        if (field) next.field = field
        else delete next.field
        return next
      },
    })
  }
  const select = async (work: LibraryWorkView) => {
    if (work.reviewCaseId) {
      selectCase(work.reviewCaseId)
      return
    }
    setComplete(null)
    setCompletionInspection(null)
    const generation = ++workSelectionGeneration.current
    await navigate({
      search: (current) => {
        const next = { ...current, work: work.workId }
        delete next.preview
        delete next.field
        return next
      },
    })
    if (generation !== workSelectionGeneration.current) return
    setOpenedWork(work)
  }
  // Progressive filtering keeps the typed text local so each keystroke paints.
  // A short bounded delay avoids loading an obsolete clear before the next
  // typed value; the URL remains the owner after the committed replacement.
  const scheduleSearchNavigation = () => {
    if (searchCommitTimer.current !== null) window.clearTimeout(searchCommitTimer.current)
    searchCommitTimer.current = window.setTimeout(() => {
      searchCommitTimer.current = null
      if (searchNavigation.current !== null || pendingSearchQuery.current === null) return
      const generation = searchGeneration.current
      const drain = async () => {
        try {
          while (generation === searchGeneration.current && pendingSearchQuery.current !== null) {
            const pending = pendingSearchQuery.current
            pendingSearchQuery.current = null
            await navigate({
              replace: true,
              search: (current) => {
                const next = { ...current }
                delete next.cursor
                if (pending) next.q = pending
                else delete next.q
                expectedSearchIdentity.current = librarySearchIdentity(next)
                return next
              },
            })
          }
        } catch {
          // A later owner edit may start a fresh drain; a failed read navigation
          // is never retried without that new intent.
        } finally {
          searchNavigation.current = null
          if (generation === searchGeneration.current) {
            expectedSearchIdentity.current = null
            if (pendingSearchQuery.current !== null) scheduleSearchNavigation()
            else setSearchPending(false)
          } else if (pendingSearchQuery.current !== null) {
            scheduleSearchNavigation()
          }
        }
      }
      searchNavigation.current = drain()
    }, searchNavigationDelay)
  }
  const runSearch = (value: string) => {
    const q = value.trim()
    setTypedQuery({ echo: q, text: value })
    setSearchPending(true)
    pendingSearchQuery.current = q
    scheduleSearchNavigation()
  }
  const submitSearch = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    runSearch(queryText)
  }
  const movePage = (cursor: string | undefined) => {
    void navigate({
      search: (current) => {
        const next = { ...current }
        if (cursor) next.cursor = cursor
        else delete next.cursor
        return next
      },
    })
  }
  const choose = (
    field: ReviewField,
    outcome: DecisionCommand['outcome'],
    selectedValue: DecisionCommand['selectedValue'] = null,
  ) => {
    setFrozenFieldOrders((current) =>
      current[selectedReviewCaseId] === undefined
        ? { ...current, [selectedReviewCaseId]: currentActionableDestinations }
        : current,
    )
    controller.submit({
      destination: field.destination,
      outcome,
      proposalId: field.proposalId,
      selectedValue,
      targetFingerprint: field.targetFingerprint,
      idempotencyKey: requestKey(),
      ...(field.coverConsideration ? { considerationId: field.coverConsideration.id } : {}),
    })
  }
  const closeAutomaticPanel = () => {
    const closingKey = automaticPanel
    restoreAutomaticTrigger.current = true
    setAutomaticPanel(null)
    if (closingKey && automaticResults[closingKey] === 'error')
      setAutomaticResults((current) => {
        return Object.fromEntries(Object.entries(current).filter(([key]) => key !== closingKey))
      })
  }
  const retryNextWork = () => {
    if (attention.isFetchNextPageError) void attention.fetchNextPage()
    else void attention.refetch()
  }
  const clearSelection = () => {
    workSelectionGeneration.current += 1
    setComplete(null)
    setCompletionInspection(null)
    setOpenedWork(null)
    void navigate({
      search: (current) => {
        const next = { ...current }
        delete next.preview
        delete next.work
        delete next.field
        delete next.attention
        return next
      },
    })
  }
  const selectFromQueue = (reviewCaseId: string, field: string) => {
    workSelectionGeneration.current += 1
    if (reviewCaseId !== selectedReviewCaseId)
      setExpandedChoiceState({ work: reviewCaseId, keys: new Set() })
    setComplete(null)
    setCompletionInspection(null)
    setOpenedWork(null)
    void navigate({
      search: (current) => {
        const next = { ...current, preview: reviewCaseId, field }
        delete next.work
        delete next.attention
        return next
      },
    })
  }
  const expandedChoices = {
    has: (key: string) =>
      expandedChoiceState.work === selectedReviewCaseId && expandedChoiceState.keys.has(key),
  }
  const toggleChoiceEvidence = (key: string) =>
    setExpandedChoiceState((current) => {
      const next = current.work === selectedReviewCaseId ? new Set(current.keys) : new Set<string>()
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return { work: selectedReviewCaseId, keys: next }
    })

  const effect = selected ? effectSummary(selected) : undefined
  const selectedRecorded =
    selected !== undefined &&
    (selected.humanDecision !== null || selected.automaticDecision !== null)
  const selectedActionable = selected !== undefined && actionable(selected)
  const selectedRevisitable =
    selected !== undefined &&
    selectedRecorded &&
    selected.editable &&
    selected.decisionActions.length > 0
  const selectedChoiceMode = selectedActionable || selectedRevisitable
  const selectedProgress = selected ? controller.phase(selected.destination) : null
  // Attention membership comes from the authoritative exact case. A pending
  // or failed local command has not yet changed it.
  const remainingFields = attentionFields.length
  const railPosition =
    complete?.id === selectedReviewCaseId && completionInspection === null
      ? `${String(railFields.length)} recorded · work complete`
      : `${String(remainingFields)} remaining · field ${String(
          Math.max(1, frozenDestinations.indexOf(selected?.destination ?? '') + 1),
        )} of ${String(railFields.length)}`
  // The one-tap teaching line is for someone who has not yet seen what a tap
  // does. It says so on the first field only, and gives way to what is actually
  // happening once they have chosen.
  const teachingLine =
    selectedProgress?.phase === 'recording'
      ? 'Recording choice…'
      : selectedProgress?.phase === 'confirmed'
        ? 'Recorded. Opening the next field…'
        : selectedActionable && frozenDestinations.indexOf(selected.destination) === 0
          ? 'One tap records and opens the next field'
          : ''
  const writesActive = detail.data?.writesConsented === true

  return (
    <div className={`library-page variant-m-workspace${hasSelection ? ' has-selected-work' : ''}`}>
      <h1 className="sr-only">Library</h1>
      {!hasSelection ? (
        <header
          className="library-header"
          aria-label="Library controls"
          tabIndex={-1}
          data-route-focus="true"
        >
          <button
            className="attention-toggle"
            type="button"
            aria-expanded={search.attention === 'open'}
            onClick={() =>
              void navigate({
                search: (current) => {
                  const next = { ...current }
                  if (next.attention === 'open') delete next.attention
                  else next.attention = 'open'
                  return next
                },
              })
            }
          >
            Attention <strong>{data.counts.attention}</strong>
          </button>
        </header>
      ) : null}
      {hasSelection && search.attention !== 'open' ? (
        <div className="compact-workspace-controls">
          <button className="compact-workspace-back" type="button" onClick={clearSelection}>
            Back to works
          </button>
          <button
            type="button"
            aria-label="Attention queue"
            onClick={() =>
              void navigate({ search: (current) => ({ ...current, attention: 'open' }) })
            }
          >
            Attention queue <strong aria-hidden="true">{data.counts.attention}</strong>
          </button>
        </div>
      ) : null}
      <PreparationNotice routeIdentity={routeIdentity} />
      <WriteReadyNotice />
      <div
        className={`library-workspace${hasSelection ? ' has-selection' : ''}${search.attention === 'open' ? ' attention-open' : ''}`}
      >
        <section className="library-works" aria-labelledby="library-works-title">
          <div className="library-works-heading">
            <h2 id="library-works-title">Works</h2>
            <span>{data.counts.all} total</span>
          </div>
          <nav className="lenses" aria-label="Library lenses">
            {(['all', 'needs_attention', 'no_attention_needed'] as const).map((lens) => (
              <Link
                key={lens}
                to="/library"
                search={{
                  lens,
                  ...(search.q ? { q: search.q } : {}),
                  ...(search.preview ? { preview: search.preview } : {}),
                  ...(search.work ? { work: search.work } : {}),
                  ...(search.field ? { field: search.field } : {}),
                }}
                preload="intent"
                activeProps={{ 'aria-current': 'page' }}
              >
                {fieldLabel(lens === 'no_attention_needed' ? 'No attention needed' : lens)}
              </Link>
            ))}
          </nav>
          <form className="search library-search" role="search" onSubmit={submitSearch}>
            <label className="sr-only" htmlFor="library-query">
              Search title, author, identifier, library, or format
            </label>
            <div>
              <input
                id="library-query"
                name="q"
                type="search"
                maxLength={128}
                value={queryText}
                placeholder="Search works…"
                onChange={(event) => runSearch(event.target.value)}
              />
              <button type="button" disabled={!queryText} onClick={() => runSearch('')}>
                Clear
              </button>
            </div>
          </form>
          <div className="library-work-list">
            {data.works.length === 0 ? (
              <div className="empty-copy">
                <p>No exact-current works match this search and lens.</p>
                {search.q ? (
                  <button type="button" onClick={() => runSearch('')}>
                    Clear search
                  </button>
                ) : null}
              </div>
            ) : (
              data.works.map((work) => {
                const selectedWorkRow = work.reviewCaseId
                  ? search.preview === work.reviewCaseId
                  : search.work === work.workId
                const needsAttention = work.attentionCount > 0
                const status = work.preparation
                  ? 'Preparation failed safely'
                  : needsAttention
                    ? work.attentionCount === 1
                      ? '1 field needs attention'
                      : `${String(work.attentionCount)} fields need attention`
                    : 'No attention needed'
                const terminalFailure = work.preparation !== null && !work.reviewCaseId
                const preparation = work.preparation
                const preparationId = preparation ? `preparation-${work.id}` : undefined
                const row = (
                  <>
                    <span className="library-book-mark" aria-hidden="true">
                      {initials(work.title)}
                    </span>
                    <span className="library-work-copy">
                      <strong className="library-work-title">{work.title}</strong>
                      <span className="library-work-author">{authorLine(work)}</span>
                      <span className="library-work-origin">
                        {work.library} · {fieldLabel(work.mediaKind)}
                      </span>
                      <span className="library-work-status">
                        <span
                          className={
                            needsAttention || work.preparation ? 'needs-attention' : 'handled'
                          }
                        >
                          {status}
                        </span>
                        {preparation ? (
                          <span id={preparationId} className="terminal-preparation-copy">
                            {preparation.stage}: {preparation.reason} {preparation.recovery}{' '}
                            Reference: <code>{preparation.diagnosticReference}</code>
                          </span>
                        ) : null}
                      </span>
                    </span>
                  </>
                )
                return terminalFailure ? (
                  <article
                    key={work.id}
                    className="library-work-row is-terminal"
                    aria-label="Terminal preparation failure"
                  >
                    {row}
                  </article>
                ) : (
                  <button
                    key={work.id}
                    type="button"
                    className={`library-work-row${selectedWorkRow ? ' active' : ''}`}
                    aria-current={selectedWorkRow ? 'true' : undefined}
                    aria-label={`${work.title}. ${authorLine(work)}. ${status}`}
                    aria-describedby={preparationId}
                    onClick={() => void select(work)}
                  >
                    {row}
                  </button>
                )
              })
            )}
          </div>
          <div className="library-pager">
            <button
              type="button"
              disabled={!data.previousCursor}
              onClick={() => movePage(data.previousCursor)}
            >
              ← Previous
            </button>
            <span>
              Page {String(data.page)}
              {data.nextCursor ? '' : ' · end of the bounded current result'}
            </span>
            <button
              type="button"
              disabled={!data.nextCursor}
              onClick={() => movePage(data.nextCursor)}
            >
              Next →
            </button>
          </div>
        </section>
        <section
          className="workspace-detail"
          aria-live="polite"
          tabIndex={-1}
          {...(!selectedWork &&
          search.preview &&
          search.attention !== 'open' &&
          (!displayedIdentity || detail.isPending || detail.isError)
            ? { 'data-route-focus': 'true' }
            : {})}
        >
          {selectedWork ? (
            <WorkSummary work={selectedWork} />
          ) : !search.preview ? (
            <div className="library-selection">
              <h2>Choose a work</h2>
              <p>
                Its current values and field position stay here while you move through attention.
              </p>
            </div>
          ) : !displayedIdentity ? (
            <div className="library-selection">
              <h2>Selected work</h2>
              <p>Readable work identity is not available on this Library page.</p>
              <p role="status">This work is not available in the current Library result.</p>
            </div>
          ) : detail.isPending ? (
            <p role="status">Loading selected work…</p>
          ) : detail.isError ? (
            <p role="alert">
              Selected work could not be loaded.{' '}
              <button
                ref={selectedDetailRetryButton}
                onClick={() => {
                  selectedDetailRetry.current = search.preview ?? null
                  void detail.refetch()
                }}
              >
                Try again
              </button>{' '}
            </p>
          ) : !detail.data.current ? (
            <div className="library-selection">
              <p>Case superseded</p>
              <h2>{displayedIdentity.title}</h2>
              <p role="alert">
                This review case is no longer current. Reopen its current replacement from Library.
              </p>
            </div>
          ) : (
            <>
              <header
                className="selected-work-header"
                tabIndex={-1}
                {...(search.preview && search.attention !== 'open'
                  ? { 'data-route-focus': 'true' }
                  : {})}
              >
                <span className="library-book-mark" aria-hidden="true">
                  {initials(displayedIdentity.title)}
                </span>
                <div>
                  <h2>{displayedIdentity.title}</h2>
                  <p>
                    {displayedIdentity.authors.length
                      ? displayedIdentity.authors.join(', ')
                      : 'Author details unavailable'}{' '}
                    · {displayedIdentity.library} · {labelWords(displayedIdentity.mediaKind)}
                  </p>
                </div>
                <dl className="selected-work-status">
                  <div>
                    <dt>Needs review</dt>
                    <dd>{String(attentionFields.length)}</dd>
                  </div>
                  <div>
                    <dt>Resolved automatically</dt>
                    <dd>
                      {String(
                        detail.data.fields.filter((field) => field.automaticDecision !== null)
                          .length,
                      )}
                    </dd>
                  </div>
                </dl>
              </header>
              {detail.data.comparisonNotices.length ? (
                <aside className="comparison-notice" aria-label="Provider comparison condition">
                  {detail.data.comparisonNotices.map((notice) => (
                    <p key={notice}>{notice}</p>
                  ))}
                </aside>
              ) : null}
              <section className="field-map" aria-label={`Fields in ${displayedIdentity.title}`}>
                <header>
                  <strong>{railPosition}</strong>
                </header>
                <nav ref={fieldRail} className="field-filmstrip" aria-label="Review fields">
                  {railFields.map((field) => (
                    <button
                      key={field.destination}
                      ref={
                        selected?.destination === field.destination ? activeFieldButton : undefined
                      }
                      aria-current={selected?.destination === field.destination}
                      className={!actionable(field) ? 'is-recorded' : undefined}
                      onClick={() => {
                        if (complete?.id === selectedReviewCaseId)
                          setCompletionInspection(field.destination)
                        selectCase(selectedReviewCaseId, field.destination)
                      }}
                    >
                      <strong>{fieldLabel(field.destination)}</strong>
                      <span
                        aria-label={
                          actionable(field)
                            ? 'Needs review'
                            : field.humanDecision
                              ? 'Decision recorded'
                              : field.classification === 'automatic_recorded'
                                ? 'Handled automatically; inspect or override'
                                : fieldState(field)
                        }
                      >
                        {actionable(field) ? '•' : '✓'}
                      </span>
                    </button>
                  ))}
                </nav>
              </section>
              {complete?.id === selectedReviewCaseId && completionInspection === null ? (
                <article
                  className="review-complete"
                  aria-live="polite"
                  tabIndex={-1}
                  ref={completionRegion}
                >
                  <span className="review-complete-mark">✓</span>
                  <div>
                    <h2>Review complete</h2>
                    <span>
                      {String(complete.fields.length)} recorded field outcome
                      {complete.fields.length === 1 ? '' : 's'}
                      {writesActive
                        ? ' — each began applying the moment you decided it.'
                        : '. No library data changed.'}
                    </span>
                  </div>
                  <ol>
                    {complete.fields.map((field) => (
                      <li key={field.destination}>
                        <button
                          type="button"
                          onClick={() => {
                            setCompletionInspection(field.destination)
                            selectCase(selectedReviewCaseId, field.destination)
                          }}
                        >
                          <small>
                            {fieldLabel(field.destination)} · {completionOutcome(field)}
                          </small>
                          <strong>{decisionValue(field)}</strong>
                        </button>
                      </li>
                    ))}
                  </ol>
                  <p>
                    {writesActive
                      ? 'Every write is logged in Activity, and read-back verification remains a separate guarded stage.'
                      : 'Nothing is written yet — applying happens separately, behind your consent.'}
                  </p>
                  <footer>
                    {nextWork ? (
                      <button
                        type="button"
                        onClick={() => {
                          setComplete(null)
                          setCompletionInspection(null)
                          selectCase(nextWork.id, nextWork.destination)
                        }}
                      >
                        Review next work →
                      </button>
                    ) : attention.isPending || attention.isFetchingNextPage ? (
                      <span>Looking for the next visible work…</span>
                    ) : nextWorkError ? (
                      <>
                        <span>The next attention work could not be loaded.</span>
                        <button type="button" onClick={retryNextWork}>
                          Try finding next work again
                        </button>
                      </>
                    ) : (
                      <span>All visible review work is complete.</span>
                    )}
                  </footer>
                </article>
              ) : selected ? (
                <article
                  key={selected.destination}
                  className={`decision-canvas${controller.recording ? ' is-recording' : ''}${selectedChoiceMode ? '' : ' is-context-only'}`}
                >
                  {complete?.id === selectedReviewCaseId ? (
                    <button
                      type="button"
                      className="decision-back"
                      onClick={() => setCompletionInspection(null)}
                    >
                      ← Back to completion
                    </button>
                  ) : null}
                  <header>
                    <div>
                      <h2>
                        {selectedActionable
                          ? decisionQuestion(selected.destination)
                          : fieldLabel(selected.destination)}
                      </h2>
                      {selectedActionable ? (
                        <p>{`${fieldLabel(selected.destination)} · ${fieldReason(selected)}`}</p>
                      ) : selectedRevisitable ? (
                        <p>
                          The recorded outcome is marked below. You can select another eligible
                          value.
                        </p>
                      ) : null}
                    </div>
                    {selectedActionable ? (
                      <dl>
                        <div>
                          <dt>Queue position</dt>
                          <dd>
                            {activeQueueIndex >= 0
                              ? `${String(activeQueueIndex + 1)} of ${String(attentionItems.length)}`
                              : 'Selected work'}
                          </dd>
                        </div>
                        <div>
                          <dt>Choices</dt>
                          <dd>{String(ownerChoiceCount(selected))}</dd>
                        </div>
                      </dl>
                    ) : null}
                  </header>
                  {selectedChoiceMode && !controller.reloadRequired ? (
                    selected.destination === 'cover' ? (
                      <LibraryCoverChoices
                        reviewCaseId={selectedReviewCaseId}
                        field={selected}
                        disabled={false}
                        expandedEvidence={expandedChoices}
                        progress={selectedProgress}
                        choose={(outcome, value) => choose(selected, outcome, value)}
                        revealEvidence={toggleChoiceEvidence}
                      />
                    ) : (
                      <section
                        className="direct-choice protected-choice"
                        aria-label={
                          selectedActionable
                            ? decisionQuestion(selected.destination)
                            : `${fieldLabel(selected.destination)} recorded choices`
                        }
                      >
                        {/*
                          What a tap will do, said before the tap and in the
                          present tense of this work's actual write state. The
                          same words after the fact are what the completion
                          summary says, so the promise made here is the one kept
                          there (#639).
                        */}
                        <p className={`cm-write-state is-${writesActive ? 'on' : 'off'}`}>
                          {writesActive
                            ? 'Selecting an outcome records this field and prepares a guarded Audiobookshelf write.'
                            : 'Selecting an outcome records this field only. Audiobookshelf is unchanged until writes are enabled.'}
                        </p>
                        <div
                          className={`direct-choice-grid${selected.preservationOnly ? ' preservation-only' : ''}`}
                        >
                          {selected.decisionActions.includes('keep_current') ? (
                            <MetadataChoice
                              context={choiceContext(selected, null)}
                              current
                              disabled={false}
                              expanded={expandedChoices.has(`${selected.destination}:current`)}
                              label="Current value"
                              longDescription={isLongDescription(
                                selected.destination,
                                selected.currentValue,
                              )}
                              onChoose={() => choose(selected, 'keep_current')}
                              recording={
                                commandSelected(
                                  selectedProgress?.command ?? null,
                                  selected.destination,
                                  'keep_current',
                                ) && selectedProgress?.phase === 'recording'
                              }
                              selected={choiceSelected(selected, selectedProgress, 'keep_current')}
                              toggle={() => toggleChoiceEvidence(`${selected.destination}:current`)}
                              value={selected.currentValue}
                            />
                          ) : null}
                          {selected.decisionActions.includes('accept') &&
                          !selected.preservationOnly ? (
                            <div
                              className={`proposed-choice-list${selected.proposedValues.length === 1 ? ' has-one-choice' : ''}`}
                            >
                              <header>
                                <strong>Choose a new value</strong>
                                <small
                                  aria-live="polite"
                                  className={teachingLine ? 'is-active' : ''}
                                >
                                  {teachingLine}
                                </small>
                              </header>
                              {selected.proposedValues.map((value, index) => {
                                const context = choiceContext(
                                  selected,
                                  selected.choiceContexts.filter(
                                    (item) => item.selection === 'proposed',
                                  )[index]?.alternativeId ?? null,
                                )
                                const key = `${selected.destination}:${context?.alternativeId ?? String(index)}`
                                return (
                                  <MetadataChoice
                                    context={context}
                                    current={false}
                                    disabled={false}
                                    expanded={expandedChoices.has(key)}
                                    label={`Alternative ${String(index + 1)}`}
                                    longDescription={isLongDescription(
                                      selected.destination,
                                      displayValue(value.value),
                                    )}
                                    onChoose={() => choose(selected, 'accept', value)}
                                    recording={
                                      commandSelected(
                                        selectedProgress?.command ?? null,
                                        selected.destination,
                                        'accept',
                                        value,
                                      ) && selectedProgress?.phase === 'recording'
                                    }
                                    selected={choiceSelected(
                                      selected,
                                      selectedProgress,
                                      'accept',
                                      value,
                                    )}
                                    toggle={() => toggleChoiceEvidence(key)}
                                    value={displayValue(value.value)}
                                    key={key}
                                  />
                                )
                              })}
                            </div>
                          ) : null}
                        </div>
                      </section>
                    )
                  ) : selectedRecorded ? (
                    <RecordedOutcome
                      field={selected}
                      expanded={expandedChoices.has(`${selected.destination}:recorded`)}
                      toggle={() => toggleChoiceEvidence(`${selected.destination}:recorded`)}
                    />
                  ) : (
                    <>
                      <p className={selectedActionable ? 'decision-recorded' : 'decision-context'}>
                        {selectedActionable
                          ? `${fieldReason(selected)}${selected.humanDecision || selected.automaticDecision ? ` The recorded value remains ${decisionValue(selected)}.` : ''}`
                          : 'There’s no other value to choose from.'}
                      </p>
                      {!selectedActionable ? (
                        <ContextValue
                          field={selected}
                          expanded={expandedChoices.has(`${selected.destination}:context`)}
                          toggle={() => toggleChoiceEvidence(`${selected.destination}:context`)}
                        />
                      ) : null}
                    </>
                  )}
                  {selected.manualHold ? (
                    <section
                      className="automatic-handling-panel"
                      aria-label="Automatic handling"
                      onKeyDown={(event) => {
                        if (event.key === 'Escape' && !returnAutomatic.isPending) {
                          event.preventDefault()
                          closeAutomaticPanel()
                        }
                      }}
                    >
                      {automaticPanelVisible ? (
                        <>
                          <h3>Automatic handling is paused for this field</h3>
                          <p>
                            The recorded value remains. Fresh checks are requested first; automatic
                            policy may manage later qualified changes.
                          </p>
                          {automaticResult === 'success' ? (
                            <p role="status">
                              Fresh checks requested. Manual handling remains until they complete.
                            </p>
                          ) : automaticResult === 'error' ? (
                            <p role="alert">
                              Fresh checks could not be requested. Manual handling is unchanged.
                            </p>
                          ) : null}
                          <div className="automatic-handling-actions">
                            <button
                              ref={keepManualHandling}
                              type="button"
                              disabled={returnAutomatic.isPending}
                              onClick={closeAutomaticPanel}
                            >
                              Keep manual handling
                            </button>
                            <button
                              type="button"
                              disabled={returnAutomatic.isPending || automaticCompletedForSelected}
                              onClick={() => {
                                if (!selectedAutomaticKey) return
                                returnAutomatic.mutate({
                                  destination: selected.destination,
                                  key: selectedAutomaticKey,
                                })
                              }}
                            >
                              {returnAutomatic.isPending
                                ? 'Requesting fresh checks…'
                                : 'Resume after fresh checks'}
                            </button>
                          </div>
                        </>
                      ) : (
                        <button
                          ref={automaticTrigger}
                          type="button"
                          aria-disabled={automaticCompletedForSelected}
                          onClick={
                            automaticCompletedForSelected
                              ? undefined
                              : () => setAutomaticPanel(selectedAutomaticKey)
                          }
                        >
                          {automaticCompletedForSelected
                            ? 'Fresh checks requested'
                            : 'Resume automatic handling'}
                        </button>
                      )}
                    </section>
                  ) : null}
                  {comparisonNotices(selected).length ? (
                    <aside className="comparison-notice" aria-label="Comparison condition">
                      {comparisonNotices(selected).map((notice) => (
                        <p key={notice}>{fieldLabel(notice)}</p>
                      ))}
                    </aside>
                  ) : null}
                  {controller.canRetry && selectedProgress?.phase === 'failed' ? (
                    <button
                      ref={retryButton}
                      className="decision-retry"
                      type="button"
                      onClick={controller.retry}
                    >
                      Try the same decision again
                    </button>
                  ) : null}
                  {selectedChoiceMode && effect ? (
                    <aside className="field-apply-context" aria-label="External update status">
                      <span className="activity-state-mark" aria-hidden="true"></span>
                      <span>
                        <small>External library</small>
                        <strong>{effect.state}</strong>
                        <em>{effect.detail}</em>
                      </span>
                    </aside>
                  ) : null}
                  {selectedChoiceMode &&
                  (controller.statusFor(selected.destination) ||
                    controller.messageFor(selected.destination)) ? (
                    <p
                      role={
                        controller.decision.isError || controller.reloadRequired
                          ? 'alert'
                          : 'status'
                      }
                    >
                      {controller.statusFor(selected.destination) ||
                        controller.messageFor(selected.destination)}
                    </p>
                  ) : null}
                </article>
              ) : (
                <p>This work has no fields requiring a decision.</p>
              )}
              {contextualFields.length ? (
                <details className="automatic-resolution-details">
                  <summary>
                    <span>
                      {contextualFields.length === 1
                        ? '1 field has context'
                        : `${String(contextualFields.length)} fields have context`}
                    </span>
                    <small>Inspect context</small>
                  </summary>
                  <div className="automatic-resolution-list">
                    {contextualFields.map((field) => (
                      <div className="automatic-resolution-row" key={field.destination}>
                        <span className="automatic-state-mark" aria-hidden="true">
                          ✓
                        </span>
                        <div>
                          <strong>{fieldLabel(field.destination)}</strong>
                          <small>
                            {fieldState(field)} · {decisionValue(field)}
                          </small>
                          <small>{fieldReason(field)}</small>
                        </div>
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}
              <WorkSources
                comparison={selectedChoiceMode}
                outcomes={selectedLibraryWork?.preparationOutcomes ?? []}
              />
            </>
          )}
        </section>
        {search.attention === 'open' ? (
          <aside
            className="attention-rail"
            aria-label="Attention queue"
            tabIndex={-1}
            ref={attentionRail}
          >
            <header>
              <div>
                <h2>Attention</h2>
                <p>{String(attentionItems.length)} visible fields</p>
              </div>
              <button
                onClick={() =>
                  void navigate({
                    search: (current) => {
                      const next = { ...current }
                      delete next.attention
                      return next
                    },
                  })
                }
              >
                Close
              </button>
            </header>
            {attention.data === undefined ? (
              <p role={attention.isError ? 'alert' : 'status'}>
                {attention.isError ? (
                  <>
                    Attention could not be loaded.{' '}
                    <button type="button" onClick={() => void attention.refetch()}>
                      Try again
                    </button>
                  </>
                ) : (
                  'Loading attention…'
                )}
              </p>
            ) : (
              <>
                {attentionItems.map((item, index) => (
                  <button
                    key={`${item.id}:${item.destination}`}
                    className="attention-queue-item"
                    aria-current={
                      item.id === search.preview && item.destination === selected?.destination
                    }
                    onClick={() => selectFromQueue(item.id, item.destination)}
                  >
                    <span className="attention-position">{String(index + 1)}</span>
                    <span className="attention-queue-copy">
                      <strong>{item.title}</strong>
                      <small>
                        {item.authors.length
                          ? item.authors.join(', ')
                          : 'Author details unavailable'}
                      </small>
                      <small>
                        {item.library} · {fieldLabel(item.mediaKind)}
                      </small>
                      <b>
                        {fieldLabel(item.destination)} ·{' '}
                        {item.classificationReason ?? 'Needs your decision'}
                      </b>
                    </span>
                    <span className="attention-work-count">
                      <strong>{String(item.workAttentionCount)}</strong>
                      <small>for work</small>
                    </span>
                  </button>
                ))}
                {attention.hasNextPage ? (
                  <button
                    className="attention-load-more"
                    type="button"
                    disabled={attention.isFetchingNextPage}
                    onClick={() => void attention.fetchNextPage()}
                  >
                    {attention.isFetchingNextPage ? 'Loading more…' : 'Load more attention'}
                  </button>
                ) : null}
                {attention.isFetchNextPageError ? (
                  <p role="alert">
                    More attention could not be loaded.{' '}
                    <button type="button" onClick={() => void attention.fetchNextPage()}>
                      Try again
                    </button>
                  </p>
                ) : null}
              </>
            )}
          </aside>
        ) : null}
      </div>
    </div>
  )
}
