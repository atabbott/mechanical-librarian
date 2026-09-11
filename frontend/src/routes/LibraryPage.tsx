import { DescriptionAdvicePreparation } from '../components/DescriptionAdvice'
import { MediaHealth } from '../components/MediaHealth'
import { descriptionAdviceStatus } from '../descriptionAdviceStatus'
import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useRouter, useRouterState } from '@tanstack/react-router'
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, SyntheticEvent } from 'react'

import { displayValue } from '../api/http'
import type {
  CaseDetailView,
  ChoiceContextView,
  CoverObservationView,
  DecisionCommand,
  LibrarySearch,
  LibraryView,
  LibraryWorkView,
  PreparationView,
  ProviderOutcomeView,
} from '../api/http'
import { useCoverChoiceData } from '../coverChoices'
import { useRouteReadyFocus } from '../focus'
import { fieldLabel, labelWords, opaqueSource } from '../labels'
import { coalesceMilliseconds } from '../liveInvalidation'
import {
  detailQuery,
  libraryRoute,
  PreparationNotice,
  usePreparation,
  useReviewContext,
  normalizeDetailSearch,
} from '../router'
import { WriteReadyNotice } from '../components/WriteReadyNotice'
import { actionable, requestKey, useReviewDecisionController } from '../reviewDecisionController'
import { automaticChoiceSelected, humanChoiceSelected } from '../reviewAutomaticSelection'
import { ownerChoiceCount } from '../reviewChoiceCounts'
import { advancePresentationSettled } from '../reviewAdvancePresentation'

type ReviewField = CaseDetailView['fields'][number]
const authoredOwnerRecorded = (field: ReviewField) =>
  field.descriptionAdvice?.selection?.origin === 'human' &&
  Boolean(
    field.descriptionAdvice.selected_advice?.current === true ||
    field.descriptionAdvice.application_outcome,
  )
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
// A work with a review case opens that case; one with only a published report
// is addressed by the report itself, and the server names which rather than
// leaving it to be read off the URL's shape -- a prefix sniff would silently
// re-orphan this evidence the next time a route is renamed. Deciding it once
// here keeps the row and the panel agreeing on what is openable.
const reportEvidenceUrl = (work: LibraryWorkView) =>
  work.detailKind === 'report' ? work.detailUrl : null
const chapterSpan = (start: number, end: number) => {
  const clock = (value: number) => {
    const seconds = Math.floor(value / 1000)
    const parts = [Math.floor(seconds / 3600), Math.floor((seconds % 3600) / 60), seconds % 60]
    return parts.map((part) => String(part).padStart(2, '0')).join(':')
  }
  return `${clock(start)} – ${clock(end)}`
}
// The server's closed lane status is an audit token, not something an owner can
// read: `candidates` and `candidate_limit_exceeded` differ by one word and mean
// opposite things. The consequence beside it is the server's own owner-facing
// sentence; this names the outcome the owner is looking for.
// The static map is exercised directly by the source-status content proof.
// eslint-disable-next-line react-refresh/only-export-components
export const sourceStatusLabel = (status: string) =>
  ({
    __proto__: null,
    candidates: 'Candidates found',
    no_match: 'No match',
    disabled: 'Not enabled',
    insufficient_input: 'Not enough identity',
    authentication: 'Authentication failed',
    authorization: 'Access denied',
    rate_limited: 'Rate limited',
    unavailable: 'Unavailable',
    incomplete: 'Incomplete evidence',
    malformed: 'Malformed evidence',
    oversized: 'Response too large',
    candidate_limit_exceeded: 'Too many candidates',
    query_limit_exceeded: 'Query allowance reached',
  })[status] ?? 'Provider failure'
const reviewerUnavailableDetail = (reason: string | null) =>
  reason === 'profile contract requires memory_bytes=1073741824'
    ? 'The reviewer profile must set memory_bytes to 1073741824. Regenerate the profile for the current contract, replace the configured profile, then restart the service.'
    : reason === 'profile contract requires process_limit=32'
      ? 'The reviewer profile must set process_limit to 32. Regenerate the profile for the current contract, replace the configured profile, then restart the service.'
      : null
const initials = (title: string) =>
  title
    .split(/\s+/u)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join('') || 'ML'
type PreparationPresentation = NonNullable<PreparationView['presentation']>
const preparationStateCopy: Record<PreparationPresentation['state'], string> = {
  starting: 'Preparation is starting',
  running: 'Preparation continues',
  blocked: 'Preparation needs recovery',
  completed_clean: 'Preparation is complete',
  completed_with_failures: 'Preparation completed with failures',
  dismissed: 'Preparation dismissed',
}
const preparationTriggerCopy: Record<
  PreparationPresentation['triggers'][number]['trigger'],
  string
> = {
  initial_setup: 'Initial setup',
  manual_scan: 'Owner scan',
  library_added: 'Library added',
}
const preparationPassage = [
  ['read', 'Read from library'],
  ['searches_finished', 'Searches finished'],
  ['compared', 'Candidates compared'],
  ['checked', 'Metadata checked'],
] as const

const PreparationSummary = ({
  presentation,
  reconnecting,
  selected,
  onSelect,
}: {
  presentation: PreparationPresentation
  reconnecting: boolean
  selected: boolean
  onSelect: () => void
}) => {
  const active = presentation.active_books.length + presentation.active_overflow
  const outcomes = `${String(presentation.passage.ready)} ready · ${String(presentation.passage.failed)} could not finish`
  let title: string
  let copy: string
  switch (presentation.state) {
    case 'starting':
      title = 'Preparation is starting'
      copy = `${outcomes} · ${String(active)} active`
      break
    case 'running':
      title = 'Preparing your library'
      copy = `${outcomes} · ${String(active)} active`
      break
    case 'blocked':
      title = 'Preparation needs attention'
      copy = `${outcomes} · ${String(active)} active`
      break
    case 'completed_clean':
      title = 'Preparation complete'
      copy = `${String(presentation.passage.ready)} ready · No failures`
      break
    case 'completed_with_failures':
      title = 'Preparation completed with failures'
      copy = outcomes
      break
    case 'dismissed':
      title = 'Preparation dismissed'
      copy = outcomes
      break
  }
  return (
    <button
      type="button"
      className={`preparation-summary state-${presentation.state}${selected ? ' active' : ''}`}
      aria-current={selected ? 'true' : undefined}
      onClick={onSelect}
    >
      <span className="preparation-summary-mark" aria-hidden="true" />
      <span>
        <strong>{title}</strong>
        <small>{reconnecting ? 'Reconnecting · last confirmed progress shown' : copy}</small>
      </span>
    </button>
  )
}

const PreparationDetail = ({
  presentation,
  reconnecting,
  animateChange,
  dismissing,
  onDismiss,
}: {
  presentation: PreparationPresentation
  reconnecting: boolean
  animateChange: boolean
  dismissing: boolean
  onDismiss: () => void
}) => {
  const outcomeTotal = presentation.passage.ready + presentation.passage.failed
  let heading: string
  let introduction: string
  switch (presentation.state) {
    case 'starting':
      heading = 'Preparation is starting'
      introduction = 'The run is open and waiting for preparation work to begin or resume.'
      break
    case 'running':
      heading = 'Preparing your library'
      introduction = 'Preparation continues here while ready works remain selectable.'
      break
    case 'blocked':
      heading = 'Preparation needs attention'
      introduction = 'Preparation is paused until the reported issue is resolved.'
      break
    case 'completed_clean':
      heading = 'Preparation complete'
      introduction = 'This run finished without a reported failure.'
      break
    case 'completed_with_failures':
      heading = 'Preparation completed with failures'
      introduction =
        'This run finished with reported failures; ready books remain available and the failed outcomes stay visible.'
      break
    case 'dismissed':
      heading = 'Preparation dismissed'
      introduction = 'This preparation record has been dismissed.'
      break
  }
  return (
    <div className="preparation-detail">
      <header>
        <div>
          <h2 tabIndex={-1} data-route-focus="true">
            {heading}
          </h2>
          <p>{introduction}</p>
        </div>
        <strong>{String(presentation.passage.ready)} ready</strong>
      </header>
      <p
        className={`preparation-run-state state-${presentation.state}${animateChange ? ' reported-change' : ''}`}
        role="status"
      >
        {reconnecting
          ? 'Reconnecting · showing the last confirmed state'
          : preparationStateCopy[presentation.state]}
      </p>
      <p className="preparation-provenance">
        Triggered by{' '}
        {presentation.triggers
          .map(
            (trigger) =>
              `${preparationTriggerCopy[trigger.trigger]} ×${String(trigger.accepted_count)}`,
          )
          .join(' · ')}
      </p>
      <section aria-labelledby="now-preparing-title">
        <div className="preparation-section-heading">
          <h3 id="now-preparing-title">Now preparing</h3>
          <span>
            {String(presentation.active_books.length + presentation.active_overflow)} active
          </span>
        </div>
        {presentation.active_books.length ? (
          <ol className="preparation-active-books">
            {presentation.active_books.map((book, index) => (
              <li key={`${book.name}:${String(index)}`}>
                <span className="library-book-mark" aria-hidden="true">
                  {initials(book.name)}
                </span>
                <span>
                  <strong>{book.name}</strong>
                  <small>{book.stage}</small>
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p>No book is active at the last confirmed refresh.</p>
        )}
        {presentation.active_overflow ? (
          <p>{String(presentation.active_overflow)} more active books follow in this run.</p>
        ) : null}
      </section>
      <section aria-labelledby="preparation-progress-title">
        <div className="preparation-section-heading">
          <div>
            <h3 id="preparation-progress-title">Preparation progress</h3>
            <p>How many books made it this far?</p>
          </div>
          <strong>{presentation.throughput_per_minute.toFixed(1)} books/min</strong>
        </div>
        <dl className="preparation-passage">
          {preparationPassage.map(([field, label]) => (
            <div key={field}>
              <dt>{label}</dt>
              <dd>
                <progress
                  aria-label={`${label}: ${String(presentation.passage[field])} of ${String(presentation.total)}`}
                  max={Math.max(presentation.total, 1)}
                  value={presentation.passage[field]}
                />
                <strong>{String(presentation.passage[field])}</strong>
              </dd>
            </div>
          ))}
          <div className="preparation-outcomes">
            <dt>Ready for review</dt>
            <dd>
              <strong>{String(presentation.passage.ready)}</strong>
            </dd>
            <dt>Could not finish</dt>
            <dd>
              <strong>{String(presentation.passage.failed)}</strong>
            </dd>
          </div>
        </dl>
      </section>
      <section aria-labelledby="recent-completions-title">
        <div className="preparation-section-heading">
          <h3 id="recent-completions-title">Recent completions</h3>
          <span>{String(outcomeTotal)} outcomes</span>
        </div>
        {presentation.recent_completions.length ? (
          <ul className="preparation-completions">
            {presentation.recent_completions.map((completion, index) => (
              <li key={`${completion.name}:${String(completion.completed_at)}:${String(index)}`}>
                <span>{completion.name}</span>
                <strong>{completion.outcome === 'ready' ? 'Ready' : 'Could not finish'}</strong>
              </li>
            ))}
          </ul>
        ) : (
          <p>No terminal book has been reported yet.</p>
        )}
      </section>
      {presentation.dismissible ? (
        <button type="button" disabled={dismissing} onClick={onDismiss}>
          {dismissing ? 'Dismissing…' : 'Dismiss preparation'}
        </button>
      ) : null}
    </div>
  )
}
const bounded = (value: string, fallback: string) =>
  opaqueSource.test(value) ? fallback : value.length > 160 ? `${value.slice(0, 157)}…` : value
const decisionValue = (field: ReviewField) => {
  const selected = (field.humanDecision ?? field.automaticDecision)?.selected_value
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
const effectSummary = (field: ReviewField) => {
  const advice = field.destination === 'description' ? field.descriptionAdvice : null
  if (advice?.selection) {
    if (advice.application_outcome === 'verified')
      return {
        state: 'Applied and verified',
        detail:
          'This authored description was applied and verified in the library. Its record is retained in History.',
      }
    if (advice.application_outcome === 'no_change_required')
      return {
        state: 'No change needed',
        detail:
          'The library already held the selected description. This authored choice is retained in History.',
      }
    if (!advice.selected_advice?.current)
      return {
        state: 'Earlier choice retained',
        detail:
          'This choice is no longer current. History retains the choice; Activity shows any recorded application progress.',
      }
    return field.effectCapability === 'allowed'
      ? {
          state: 'Choice recorded',
          detail:
            'Activity shows the current status of this description change. Backup, currentness, validation and read-back checks remain required for an external update.',
        }
      : field.effectCapability === 'unavailable'
        ? {
            state: 'Choice recorded · External update unavailable',
            detail:
              'No external update capability is configured. Activity shows any recorded application progress.',
          }
        : {
            state: 'Choice recorded · External update guarded',
            detail: `${fieldLabel(field.effectCapability)} currently prevents an external update. Activity shows any recorded application progress.`,
          }
  }
  return field.effectCapability === 'allowed'
    ? {
        state: 'Eligible for guarded planning',
        detail:
          'External updates require backup, currentness, validation and read-back checks. Activity shows any prepared changes.',
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
  !command.authoredDescription &&
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
  field.descriptionAdvice &&
  (field.descriptionAdvice.selected_advice?.current === true ||
    field.descriptionAdvice.application_outcome !== null) &&
  field.descriptionAdvice.selection
    ? false
    : field.humanDecision !== null
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
  'isbn_creator_supported_title',
  'isbn_title_material_conflict',
  'bibliographic_combination_unresolved',
  'competing_evidence',
  'date_refinement_requires_corroboration',
  'date_year_conflict',
  'field_policy_conflict',
  'insufficient_independent_corroboration',
  'no_safe_bibliographic_combination',
  'populated_subjective_field',
  'provider_consensus_title_bracket_source_tag',
  'provider_consensus_title_explicit_identifier',
  'provider_consensus_title_parenthesized_bare_number',
  'provider_consensus_title_parenthesized_series_name',
  'provider_consensus_title_parenthesized_year',
  'provider_consensus_title_subtitle_segment',
  'provider_consensus_title_colon_unused',
  'provider_consensus_title_series_unpreserved',
  'decoration_equivalent_bracket_source_tag',
  'decoration_equivalent_explicit_identifier',
  'decoration_equivalent_parenthesized_bare_number',
  'decoration_equivalent_parenthesized_series_name',
  'decoration_equivalent_parenthesized_year',
  'decoration_equivalent_subtitle_segment',
  'decoration_tidy_bracket_source_tag',
  'decoration_tidy_explicit_identifier',
  'decoration_tidy_parenthesized_bare_number',
  'decoration_tidy_parenthesized_series_name',
  'decoration_tidy_parenthesized_year',
  'decoration_tidy_subtitle_segment',
  'decoration_tidy_series_unpreserved',
  'decoration_equivalence_provider_dissent',
  'decoration_equivalence_decorated_consensus',
  'subtitle_segment_uncorroborated',
  'subtitle_segment_provider_dissent',
  'subtitle_segment_decorated_consensus',
  'provider_consensus_contributor_display',
])
const comparisonNotices = (field: ReviewField) =>
  field.choiceContexts.some((context) =>
    context.limitations.some((limitation) => materialComparisonReasons.has(limitation)),
  )
    ? []
    : field.notices.filter((notice) => materialComparisonReasons.has(notice))

// Match the persisted Library ordering; recovery eligibility is a separate contract.
const limitingProviderStatuses = new Set([
  'authentication',
  'authorization',
  'rate_limited',
  'unavailable',
  'incomplete',
  'malformed',
  'oversized',
  'candidate_limit_exceeded',
  'query_limit_exceeded',
])
const providerGroup = (work: LibraryWorkView) =>
  work.attentionCount === 0
    ? 'No attention needed'
    : work.preparationOutcomes.some((outcome) => limitingProviderStatuses.has(outcome.status))
      ? 'Provider evidence limited'
      : 'Provider evidence complete'
const providerSummary = (outcomes: readonly ProviderOutcomeView[]) => {
  const counts = new Map<string, number>()
  for (const outcome of outcomes) {
    const label = sourceStatusLabel(outcome.status)
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  return [...counts]
    .map(([label, count]) => `${label}${count > 1 ? ` × ${String(count)}` : ''}`)
    .join(' · ')
}

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

// The evidence a work with no review case carries. It is recorded, retained,
// and -- until this read -- reachable from nowhere in the browser: an audiobook
// acquires no case, so `detail_url` on its row is the only route it has.
// Read-only by construction: nothing here proposes, decides, or writes.
function WorkEvidence({ detailUrl }: { detailUrl: string }) {
  const { http } = useReviewContext()
  const report = useQuery({
    queryKey: ['library-report', detailUrl],
    queryFn: ({ signal }) => http.getReportDetail(detailUrl, signal),
    staleTime: 15_000,
    retry: false,
  })
  if (report.isPending)
    return (
      <p role="status" className="library-work-summary-reason">
        Loading recorded evidence…
      </p>
    )
  if (report.isError)
    return (
      <p role="alert" className="library-work-summary-reason">
        Recorded evidence could not be loaded.{' '}
        <button type="button" onClick={() => void report.refetch()}>
          Try again
        </button>
      </p>
    )
  const data = report.data
  return (
    <section className="library-work-evidence" aria-label="Recorded evidence">
      <h3>Recorded evidence</h3>
      {data.evidence.length === 0 ? (
        <p role="status">No local evidence was recorded for this work.</p>
      ) : (
        <dl className="library-work-values">
          {data.evidence.map((claim, index) => (
            <div key={`${claim.kind}-${claim.basis}-${String(index)}`}>
              <dt>
                {fieldLabel(claim.kind)}
                {claim.role ? ` · ${fieldLabel(claim.role)}` : ''}
              </dt>
              <dd>
                {claim.value}
                <small>
                  {fieldLabel(claim.basis)}
                  {claim.state === 'valid' ? '' : ` · ${fieldLabel(claim.state)}`}
                </small>
              </dd>
            </div>
          ))}
        </dl>
      )}
      {data.evidenceTruncated ? (
        <p role="status">
          Only the first {String(data.evidence.length)} recorded observations are shown.
        </p>
      ) : null}
      <MediaHealth observations={data.mediaHealth} />
      <p className="library-work-cover-presence">
        {data.coverPresent
          ? `Embedded cover art is present${
              data.coverSurface ? ` (${fieldLabel(data.coverSurface)})` : ''
            }.`
          : 'No embedded cover art was recorded.'}
      </p>
      {data.chapters.length ? (
        <details className="library-work-chapters">
          <summary>
            Chapters ({String(data.chapters.length)}
            {data.chaptersTruncated ? ', truncated' : ''})
          </summary>
          <ol>
            {data.chapters.map((chapter) => (
              <li key={chapter.ordinal}>
                {chapter.title ?? `Chapter ${String(chapter.ordinal)}`}
                <small>{chapterSpan(chapter.startMilliseconds, chapter.endMilliseconds)}</small>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </section>
  )
}

function WorkSummary({ work }: { work: LibraryWorkView }) {
  const summary = work.summary
  const evidenceUrl = reportEvidenceUrl(work)
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
      {evidenceUrl ? <WorkEvidence detailUrl={evidenceUrl} /> : null}
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

const revealFocusedCoverChoice = (event: SyntheticEvent<HTMLElement>) => {
  const choice = event.currentTarget
  const track = choice.closest<HTMLElement>('.library-cover-layout, .library-cover-pinned')
  if (!track) return
  const trackBounds = track.getBoundingClientRect()
  const choiceBounds = choice.getBoundingClientRect()
  const delta =
    choiceBounds.left < trackBounds.left
      ? choiceBounds.left - trackBounds.left
      : choiceBounds.right > trackBounds.right
        ? choiceBounds.right - trackBounds.right
        : 0
  if (delta !== 0) track.scrollLeft += delta
}

const isLongDescription = (destination: string, value: string) =>
  destination === 'description' && value.length > 280

const decisionQuestion = (destination: string) =>
  `Which ${labelWords(destination)} should this work use?`

function MetadataChoice({
  advice,
  retainedAdvice = false,
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
  advice?: ReviewField['descriptionAdvice']
  retainedAdvice?: boolean
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
  const adviceSource =
    retainedAdvice && advice?.selected_advice ? advice.selected_advice.candidate : advice
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
        aria-label={`Choose ${advice ? 'advised' : current ? 'current' : 'proposed'} value: ${value}`}
        aria-pressed={selected}
        disabled={disabled}
        onClick={onChoose}
      >
        <small>
          {label}
          {context?.recommendation === 'recommended' ? ' · Recommended' : ''}
        </small>
        <strong>{longDescription ? `${value.slice(0, 240)}…` : value}</strong>
        <span>
          {adviceSource
            ? `${adviceSource.engine} · ${adviceSource.model}`
            : (context?.rationale ?? 'Retained for this owner comparison.')}
        </span>
        <span className="choice-recommendation">
          {recording
            ? 'Recording…'
            : advice
              ? descriptionAdviceStatus(advice, retainedAdvice)
              : context?.recommendation === 'recommended'
                ? context.recommendationReason
                : 'No recommendation retained'}
        </span>
      </button>
      {longDescription ? (
        <details className="choice-description">
          <summary>Read full description</summary>
          <p
            aria-label={`Choose ${advice ? 'advised' : current ? 'current' : 'proposed'} full description`}
            className="choice-description-value"
            role={disabled ? undefined : 'button'}
            tabIndex={disabled ? undefined : 0}
            onClick={disabled ? undefined : onChoose}
            onKeyDown={(event) => {
              if (disabled || (event.key !== 'Enter' && event.key !== ' ')) return
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

function ProviderConsensusAutomaticStatement({ field }: { field: ReviewField }) {
  const decision = field.automaticDecision
  if (
    field.humanDecision !== null ||
    decision?.outcome !== 'candidate' ||
    !field.notices.includes('provider_consensus_contributor_display')
  ) {
    return null
  }
  return (
    <p>
      Providers chose {decisionValue(field)}; normalized from {field.currentValue}.
    </p>
  )
}

function DescriptionAutomaticStatement({ field }: { field: ReviewField }) {
  const decision = field.automaticDecision
  if (
    field.humanDecision !== null ||
    field.destination !== 'description' ||
    decision?.outcome !== 'candidate' ||
    field.proposedValues.length < 2
  ) {
    return null
  }
  const providers = field.automaticSelectionSources ?? []
  if (!providers.length) return null
  return (
    <p>Selected automatically from {providers.join(' and ')} after matching the target work.</p>
  )
}

function VerifiedManifestationIsbnAutomaticStatement({ field }: { field: ReviewField }) {
  if (
    field.humanDecision !== null ||
    field.destination !== 'isbn' ||
    field.automaticDecision?.outcome !== 'candidate' ||
    !field.notices.includes('verified_manifestation_isbn')
  ) {
    return null
  }
  return <p>Deterministic identity evidence bound this ISBN to this file&apos;s work.</p>
}

function SeriesAutomaticStatement({ field }: { field: ReviewField }) {
  const providers = field.automaticSelectionSources ?? []
  if (
    field.humanDecision !== null ||
    field.destination !== 'series' ||
    field.automaticDecision?.outcome !== 'candidate' ||
    !providers.length ||
    !(field.currentRaw == null || (Array.isArray(field.currentRaw) && !field.currentRaw.length))
  ) {
    return null
  }
  const memberships = field.automaticDecision.selected_value?.value
  const hasNumber =
    Array.isArray(memberships) &&
    memberships.some(
      (membership: unknown) =>
        typeof membership === 'object' &&
        membership !== null &&
        'sequence' in membership &&
        typeof membership.sequence === 'string',
    )
  return (
    <p>
      Selected from {providers.join(' and ')} to fill an empty Series field under a qualified
      automatic policy.{' '}
      {hasNumber
        ? 'Numbering is supplied by the provider and is not independently verified for this book.'
        : 'No series number was supplied.'}
    </p>
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
  const selectedProposed =
    (decision.outcome === 'accept' || decision.outcome === 'candidate') && proposed >= 0
  const context = selectedProposed
    ? choiceContext(
        field,
        field.choiceContexts.filter((item) => item.selection === 'proposed')[proposed]
          ?.alternativeId ?? null,
      )
    : choiceContext(field, null)
  const evidenceId = `recorded-evidence-${field.destination}`
  const accepted = decision.outcome === 'accept' || decision.outcome === 'candidate'
  return (
    <section
      className="recorded-outcome"
      aria-label={`${fieldLabel(field.destination)} recorded outcome`}
    >
      <p>
        <strong>
          {field.humanDecision === null && field.automaticDecision !== null
            ? 'Resolved automatically'
            : completionOutcome(field)}
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
      <ProviderConsensusAutomaticStatement field={field} />
      <DescriptionAutomaticStatement field={field} />
      <SeriesAutomaticStatement field={field} />
      <VerifiedManifestationIsbnAutomaticStatement field={field} />
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
  mediaKind: string
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

const coverObservationLabel = (value: string) =>
  ({
    absent: 'Absent',
    audiobook: 'Audiobook branding',
    clean: 'Clean reproduction',
    degraded: 'Degraded reproduction',
    english: 'English',
    explicit: 'Explicit',
    illustrated: 'Illustrated',
    illegible: 'Illegible',
    legible: 'Legible',
    minimal: 'Minimal',
    mixed: 'Mixed media',
    multiple: 'Multiple languages',
    no_visible_title: 'No visible title',
    non_english: 'Non-English',
    omnibus_or_set: 'Omnibus or set',
    photographic: 'Photographic',
    present: 'Present',
    series_branded: 'Series branded',
    standalone: 'Standalone',
    suggestive: 'Suggestive',
    text_edition: 'Text-edition branding',
    typographic: 'Typographic',
    unknown: 'Uncertain',
  })[value] ?? fieldLabel(value)

function CoverObservationEvidence({
  label,
  mediaKind,
  observation,
}: {
  label: string
  mediaKind: string
  observation: CoverObservationView | null
}) {
  if (observation === null)
    return (
      <section className="cover-observation is-unavailable" aria-label={`${label} visual checks`}>
        <strong>Visual checks unavailable</strong>
        <small>No complete reviewer evidence was recorded for this cover.</small>
      </section>
    )
  const expectedBranding =
    mediaKind === 'audiobook'
      ? 'audiobook'
      : mediaKind === 'epub' || mediaKind === 'ebook'
        ? 'text_edition'
        : null
  const wrongFormatBranding =
    expectedBranding !== null &&
    observation.formatBranding !== 'absent' &&
    observation.formatBranding !== 'unknown' &&
    observation.formatBranding !== expectedBranding
      ? coverObservationLabel(observation.formatBranding)
      : null
  const defects = [
    observation.placeholder === 'present' ? 'Placeholder artwork' : null,
    observation.reproduction === 'degraded' ? 'Degraded reproduction' : null,
    observation.overlayMarking === 'present' ? 'Overlay or marking' : null,
    wrongFormatBranding,
  ].filter((value): value is string => value !== null)
  const uncertain = [
    observation.placeholder === 'unknown' ? 'Placeholder check' : null,
    observation.reproduction === 'unknown' ? 'Reproduction check' : null,
    observation.overlayMarking === 'unknown' ? 'Overlay check' : null,
    observation.formatBranding === 'unknown' ? 'Format-branding check' : null,
  ].filter((value): value is string => value !== null)
  const defectRows = [
    ['Placeholder', observation.placeholder],
    ['Reproduction', observation.reproduction],
    ['Overlay or marking', observation.overlayMarking],
    ['Format branding', observation.formatBranding],
  ] as const
  const preferenceRows = [
    ['Composition', observation.composition],
    ['Artwork', observation.medium],
    ['Promotional text', observation.promotionalText],
    ['Adaptation branding', observation.tieInBranding],
    ['Explicit imagery', observation.explicitImagery],
    ['Title-text language', observation.coverTextLanguage],
    ['Series styling', observation.seriesStyling],
    ['Small-preview title', observation.thumbnailLegibility],
  ] as const
  return (
    <section className="cover-observation" aria-label={`${label} visual checks`}>
      <div
        className={`cover-defect-summary${defects.length ? ' has-defects' : uncertain.length ? ' is-uncertain' : ' is-clean'}`}
      >
        <strong>
          {defects.length
            ? `${String(defects.length)} visual ${defects.length === 1 ? 'issue' : 'issues'} flagged`
            : uncertain.length
              ? 'Some visual checks are uncertain'
              : 'No visual defects flagged'}
        </strong>
        {defects.length || uncertain.length ? (
          <ul>
            {defects.map((defect) => (
              <li key={defect}>{defect}</li>
            ))}
            {uncertain.map((item) => (
              <li className="is-uncertain" key={item}>
                {item} uncertain
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <details className="cover-observation-details">
        <summary>Visual observation details</summary>
        <div>
          <section aria-label="Defect checks">
            <strong>Defect checks</strong>
            <dl>
              {defectRows.map(([name, value]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{coverObservationLabel(value)}</dd>
                </div>
              ))}
            </dl>
          </section>
          <section className="cover-preference-notes" aria-label="Style and advisory notes">
            <strong>Style and advisory notes</strong>
            <dl>
              {preferenceRows.map(([name, value]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{coverObservationLabel(value)}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>
      </details>
    </section>
  )
}

function LibraryCoverChoicesContent({
  reviewCaseId,
  mediaKind,
  field,
  disabled,
  expandedEvidence,
  progress,
  revealEvidence,
  choose,
}: {
  reviewCaseId: string
  mediaKind: string
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
  if (consideration?.current !== true)
    return (
      <section aria-label="Prepare cover comparison">
        <p role="status">
          No current cover comparison is available. The existing cover remains unchanged.
        </p>
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
  const candidates = consideration.candidates
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
  const recommendedReference =
    policyStatus === 'recommended' &&
    typeof consideration.policy?.recommended_candidate_reference === 'string'
      ? consideration.policy.recommended_candidate_reference
      : null
  const policyReasons = Array.isArray(consideration.policy?.reasons)
    ? consideration.policy.reasons
        .filter((reason): reason is string => typeof reason === 'string')
        .slice(0, 2)
    : []
  const excludedReason = (candidate: (typeof candidates)[number]) =>
    candidate.matchState === 'match_not_confirmed'
      ? "Match not confirmed · We couldn't confirm that this cover belongs to this book or edition."
      : candidate.observation === null
        ? 'Reviewer observations unavailable; deterministic match evidence is retained.'
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
    const eligibleCandidate = candidate.incumbent || candidate.selectable
    const actionableCandidate = candidate.incumbent ? keeps : accepts && candidate.selectable
    const recommended = candidate.reference === recommendedReference
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
        className={`cover-choice${open ? ' is-evidence-open' : ''}${eligibleCandidate ? '' : ' is-unconfirmed'}`}
        key={candidate.reference}
        onFocusCapture={revealFocusedCoverChoice}
      >
        <button
          type="button"
          aria-pressed={actionableCandidate ? selected : false}
          className={`library-cover-card${candidate.incumbent ? ' is-current' : ''}${recommended ? ' is-recommended' : ''}${selected ? ' is-selected' : ''}`}
          disabled={disabled || !actionableCandidate}
          onClick={() => {
            if (actionableCandidate) choose(outcome, value)
          }}
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
              {recommended ? ' · Recommended' : ''}
            </small>
            <strong>
              {candidate.matchState === 'same_edition'
                ? 'Same edition'
                : candidate.matchState === 'same_book'
                  ? 'Same book'
                  : 'Match not confirmed'}
            </strong>
            <em>
              {context?.rationale ??
                `${String(candidate.width)} × ${String(candidate.height)} · ${candidate.sourceKinds.map(fieldLabel).join(', ')}`}
            </em>
            <span className="choice-recommendation">
              {!eligibleCandidate
                ? excludedReason(candidate)
                : recommended
                  ? (context?.recommendationReason ?? 'Deterministic recommendation retained')
                  : 'No recommendation retained'}
            </span>
            {selected && progress?.phase === 'recording' ? <b>Recording…</b> : null}
          </span>
        </button>
        <CoverObservationEvidence
          label={label}
          mediaKind={mediaKind}
          observation={candidate.observation}
        />
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
            {...(disabled || !actionableCandidate
              ? {}
              : { onChoose: () => choose(outcome, value) })}
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
        ? 'Deterministic confirmed choice remains available'
        : 'Manual cover comparison'
  const reviewerDetail = reviewerUnavailableDetail(consideration.reviewerUnavailableReason)
  return (
    <section
      className={`library-cover-comparison${expanded ? ' is-expanded' : ''}`}
      aria-label="Choose a cover"
    >
      <header>
        <strong>
          {recommendedReference !== null
            ? 'Recommended cover ready'
            : consideration.ready
              ? 'Cover choices ready'
              : consideration.providerFetching
                ? 'Waiting for active cover searches'
                : 'No confirmed cover recommendation'}
        </strong>
        <small>
          {recommendedReference !== null
            ? 'The technically best confirmed match is ready for your decision.'
            : consideration.ready && policyReasons.includes('actual_target_unverified')
              ? 'The current cover could not be verified, so automatic replacement is withheld.'
              : consideration.ready && policyReasons.includes('confirmed_incumbent_not_improved')
                ? 'No confirmed alternative improves the current cover, so automatic replacement is withheld.'
                : consideration.providerFetching
                  ? 'Confirmed matches remain visible; the shared decision gate opens when fetching pauses.'
                  : 'Only confirmed matches can be recommended.'}
        </small>
      </header>
      <p className="reviewer-unavailable-detail">{reviewerLabel}</p>
      {field.humanDecision ? (
        <p className="reviewer-unavailable-detail">Owner choice is final for this work.</p>
      ) : field.automaticDecision ? (
        <p className="reviewer-unavailable-detail">
          Automatically selected. Only a strictly better confirmed cover can update this choice.
        </p>
      ) : null}
      {reviewerDetail ? <p className="reviewer-unavailable-detail">{reviewerDetail}</p> : null}
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
              <strong>Retrieved alternatives</strong>
              <small>{String(alternatives.length)} available</small>
            </header>
            <div>
              {alternatives.map((candidate) => card(candidate, candidates.indexOf(candidate)))}
            </div>
          </div>
        </div>
      ) : (
        <div className="library-cover-layout">
          {consideration.incumbent.state === 'unavailable' && !incumbent ? (
            <article className="cover-choice" onFocusCapture={revealFocusedCoverChoice}>
              <button
                type="button"
                aria-pressed={choiceSelected(field, progress, 'keep_current')}
                className={`library-cover-card is-current${choiceSelected(field, progress, 'keep_current') ? ' is-selected' : ''}`}
                disabled={disabled || !keeps}
                onClick={() => {
                  if (keeps) choose('keep_current')
                }}
              >
                <span
                  className="library-cover-image"
                  role="img"
                  aria-label="Current cover unavailable"
                >
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
                  {...(disabled || !keeps ? {} : { onChoose: () => choose('keep_current') })}
                />
              ) : null}
            </article>
          ) : null}
          {candidates.map(card)}
        </div>
      )}
      {consideration.omissions.length ? (
        <details className="library-cover-excluded">
          <summary>
            {String(consideration.omissions.length)} unavailable candidate
            {consideration.omissions.length === 1 ? '' : 's'} <small>Reasons retained</small>
          </summary>
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
  const router = useRouter()
  const { http, updates } = useReviewContext()
  const preparationState = usePreparation()
  const presentation =
    preparationState.data?.presentation?.state === 'dismissed'
      ? null
      : (preparationState.data?.presentation ?? null)
  const preparationSelected = presentation !== null && search.preparation === presentation.run_id
  const presentationVersion = presentation
    ? JSON.stringify({
        run_id: presentation.run_id,
        state: presentation.state,
        triggers: presentation.triggers.map(({ trigger, accepted_count }) => ({
          trigger,
          accepted_count,
        })),
        passage: presentation.passage,
        active_books: presentation.active_books,
        active_overflow: presentation.active_overflow,
        recent_completions: presentation.recent_completions.map(({ name, outcome }) => ({
          name,
          outcome,
        })),
        total: presentation.total,
        dismissible: presentation.dismissible,
      })
    : null
  const adoptedPresentationVersion = useRef<string | null>(null)
  const [animatedPresentationVersion, setAnimatedPresentationVersion] = useState<string | null>(
    null,
  )
  useEffect(() => {
    if (presentationVersion === null) return
    if (adoptedPresentationVersion.current === null) {
      adoptedPresentationVersion.current = presentationVersion
      return
    }
    if (adoptedPresentationVersion.current === presentationVersion) return
    adoptedPresentationVersion.current = presentationVersion
    if (!preparationSelected) return
    let timer: number | null = null
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setAnimatedPresentationVersion(presentationVersion)
      timer = window.setTimeout(() => setAnimatedPresentationVersion(null), 400)
    })
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [preparationSelected, presentationVersion])
  const currentSearch = useRef(search)
  const routeNavigationGeneration = useRef(0)
  const mounted = useRef(false)
  useLayoutEffect(() => {
    mounted.current = true
    const unsubscribe = router.subscribe('onBeforeNavigate', () => {
      routeNavigationGeneration.current += 1
    })
    return () => {
      mounted.current = false
      unsubscribe()
    }
  }, [router])
  const dismissalCleanupSuppression = useRef<{
    runId: string
    navigationGeneration: number
    locationRevision: string
    searchIdentity: string
  } | null>(null)
  const dismissPreparation = useMutation({
    mutationFn: (request: {
      runId: string
      navigationGeneration: number
      locationRevision: string
      searchIdentity: string
    }) => http.dismissPreparation(request.runId),
    retry: false,
    onSuccess: async (value, request) => {
      dismissalCleanupSuppression.current = request
      if (!preparationState.adoptDismissal(request.runId, value)) {
        if (dismissalCleanupSuppression.current === request)
          dismissalCleanupSuppression.current = null
        return
      }
      const currentLocation = router.state.location
      const currentSearch = currentLocation.search as LibrarySearch
      if (
        !mounted.current ||
        routeNavigationGeneration.current !== request.navigationGeneration ||
        (currentLocation.state.__TSR_key ?? currentLocation.state.key ?? currentLocation.href) !==
          request.locationRevision ||
        librarySearchIdentity(currentSearch) !== request.searchIdentity ||
        currentSearch.preparation !== request.runId
      ) {
        if (dismissalCleanupSuppression.current === request)
          dismissalCleanupSuppression.current = null
        return
      }
      await navigate({
        replace: true,
        search: (current) => {
          if (current.preparation !== request.runId) return current
          const next = { ...current }
          delete next.preparation
          return next
        },
      })
      if (dismissalCleanupSuppression.current === request)
        dismissalCleanupSuppression.current = null
    },
  })
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
  const centreFieldRail = useCallback((rail: HTMLElement, button: HTMLButtonElement) => {
    const reducedMotion =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    // This is a horizontal rail adjustment, not a request to reposition the
    // document. `scrollIntoView` can pull the selected work back to the top
    // when the owner records a middle field.
    const railBounds = rail.getBoundingClientRect()
    const buttonBounds = button.getBoundingClientRect()
    const left = Math.max(
      0,
      rail.scrollLeft +
        buttonBounds.left -
        railBounds.left -
        (rail.clientWidth - buttonBounds.width) / 2,
    )
    if (typeof rail.scrollTo === 'function')
      rail.scrollTo({ left, behavior: reducedMotion ? 'auto' : 'smooth' })
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
  const [reconciledSuccessorIdentity, setReconciledSuccessorIdentity] = useState<string | null>(
    null,
  )
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
  const [shownEmptyFields, setShownEmptyFields] = useState<Readonly<Record<string, true>>>({})
  const [revealFocusDestination, setRevealFocusDestination] = useState<string | null>(null)
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
  const [staleCoverReview, setStaleCoverReview] = useState<{
    caseId: string
    failed: boolean
    loading: boolean
  } | null>(null)
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
  useLayoutEffect(() => {
    const suppression = dismissalCleanupSuppression.current
    if (
      suppression !== null &&
      (suppression.locationRevision !== locationRevision ||
        suppression.searchIdentity !== searchIdentity ||
        suppression.runId !== search.preparation)
    )
      dismissalCleanupSuppression.current = null
  }, [locationRevision, search.preparation, searchIdentity])
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
  useEffect(() => {
    if (!search.preparation || preparationSelected || !preparationState.confirmed) return
    if (dismissalCleanupSuppression.current?.runId === search.preparation) return
    void navigate({
      replace: true,
      search: (current) => {
        const next = { ...current }
        delete next.preparation
        return next
      },
    })
  }, [navigate, preparationSelected, preparationState.confirmed, search.preparation])
  const hasSelection = preparationSelected || Boolean(search.preview) || selectedWork !== undefined
  const selectedLibraryWork = search.preview
    ? data.works.find((work) => work.reviewCaseId === search.preview)
    : undefined
  const detail = useQuery({
    ...detailQuery(http, selectedReviewCaseId),
    enabled: Boolean(search.preview) && !preparationSelected,
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
    () =>
      attention.data?.pages.flatMap((page, pageIndex) => {
        const cursor = attention.data.pageParams[pageIndex]
        return page.items.map((item) => ({
          ...item,
          attentionCursor: typeof cursor === 'string' ? cursor : undefined,
        }))
      }) ?? [],
    [attention.data],
  )
  const selectedIdentity = useMemo(
    () =>
      search.preview
        ? (data.works.find((work) => work.reviewCaseId === search.preview) ??
          attentionItems.find((item) => item.id === search.preview) ??
          (detail.data && reconciledSuccessorIdentity === search.preview
            ? {
                id: detail.data.id,
                title: detail.data.title,
                authors: detail.data.authors,
                library: detail.data.target.library,
                mediaKind: detail.data.target.mediaKind,
              }
            : undefined))
        : undefined,
    [attentionItems, data.works, detail.data, reconciledSuccessorIdentity, search.preview],
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
          setReconciledSuccessorIdentity(successor.id)
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
  const currentActionableDestinations = visibleFields.map((field) => field.destination)
  const frozenDestinations =
    frozenFieldOrders[selectedReviewCaseId] ?? currentActionableDestinations
  const railFields = frozenDestinations
    .map((destination) => visibleFields.find((field) => field.destination === destination))
    .filter((field): field is ReviewField => field !== undefined)
  const emptyUnproposed = (field: ReviewField) =>
    (field.currentRaw === null ||
      field.currentRaw === '' ||
      (Array.isArray(field.currentRaw) && field.currentRaw.length === 0)) &&
    field.proposedValues.length === 0
  const urlSelected = visibleFields.find((field) => field.destination === search.field)
  const revealEmptyFields =
    shownEmptyFields[selectedReviewCaseId] === true ||
    (urlSelected !== undefined && emptyUnproposed(urlSelected))
  const omittedFields = revealEmptyFields ? [] : railFields.filter(emptyUnproposed)
  const renderedRailFields = revealEmptyFields
    ? railFields
    : railFields.filter((field) => !emptyUnproposed(field))
  const selected = urlSelected ?? renderedRailFields.find(actionable) ?? renderedRailFields[0]
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
    onRecorded: (caseId, destination) => {
      if (destination !== 'cover') return
      setStaleCoverReview((current) => (current?.caseId === caseId ? null : current))
    },
    onStale: async () => {
      if (selected?.destination === 'cover') {
        setStaleCoverReview({ caseId: selectedReviewCaseId, failed: false, loading: false })
        return
      }
      await navigate({ search: { lens: 'needs_attention' } })
    },
  })
  const reviewCurrentCoverComparison = async () => {
    const stale = staleCoverReview
    if (!stale || stale.loading) return
    setStaleCoverReview({ ...stale, failed: false, loading: true })
    try {
      await controller.settle()
      const successor = await http.getCaseSuccessor(stale.caseId)
      const currentCaseId = successor ?? stale.caseId
      const currentDetail = await http.getCaseDetail(currentCaseId)
      const currentCover = currentDetail.fields.find(
        (field) => field.destination === 'cover' && field.coverConsideration !== null,
      )
      if (!currentDetail.current || currentCover === undefined) throw new Error('stale cover')
      if (successor !== null) setReconciledSuccessorIdentity(currentCaseId)
      await navigate({
        replace: true,
        search: (current) =>
          current.preview === stale.caseId
            ? { ...current, preview: currentCaseId, field: 'cover' }
            : current,
      })
      setStaleCoverReview(null)
    } catch {
      setStaleCoverReview({ ...stale, failed: true, loading: false })
    }
  }
  const staleCoverNotice =
    staleCoverReview?.caseId === selectedReviewCaseId ? (
      <aside className="comparison-notice" role="alert">
        <p>This cover comparison changed before your choice was recorded. No cover was changed.</p>
        {staleCoverReview.failed ? (
          <p>The current comparison could not be loaded. Your prior choice remains unrecorded.</p>
        ) : null}
        <button
          type="button"
          disabled={staleCoverReview.loading}
          onClick={() => void reviewCurrentCoverComparison()}
        >
          {staleCoverReview.loading ? 'Loading current comparison…' : 'Review current comparison'}
        </button>
      </aside>
    ) : null
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

  const routeIdentity = `${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.attentionCursor ?? ''}:${search.preview ?? ''}:${search.work ?? ''}:${search.field ?? ''}:${search.attention ?? ''}:${search.preparation ?? ''}`
  useRouteReadyFocus(routeIdentity)
  useEffect(() => {
    if (!preparationSelected) return
    const frame = requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>('.preparation-detail [data-route-focus]')
        ?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [preparationSelected, presentation?.run_id])
  const identityPending = !displayedIdentity && attention.isPending
  const identityError = !displayedIdentity && attention.isError
  const selectedReadPending = identityPending || (Boolean(displayedIdentity) && detail.isPending)
  const selectedReadError = identityError || (Boolean(displayedIdentity) && detail.isError)
  const selectedDetailReady =
    !preparationSelected &&
    Boolean(search.preview) &&
    search.attention !== 'open' &&
    Boolean(displayedIdentity) &&
    !selectedReadPending &&
    !selectedReadError
  useEffect(() => {
    selectedDetailRetry.current = null
  }, [search.preview])
  useEffect(() => {
    const selectedDetailSettled = selectedDetailReady || selectedReadError
    if (!selectedDetailSettled) return
    const workspace = document.querySelector<HTMLElement>('#main-content .workspace-detail')
    const active = document.activeElement
    const restoreAfterRetry =
      selectedDetailRetry.current === search.preview && active === document.body
    if (workspace && active === workspace && selectedDetailReady)
      workspace.querySelector<HTMLElement>('[data-route-focus]')?.focus({ preventScroll: true })
    else if (restoreAfterRetry) {
      if (selectedReadError) selectedDetailRetryButton.current?.focus({ preventScroll: true })
      else
        workspace?.querySelector<HTMLElement>('[data-route-focus]')?.focus({ preventScroll: true })
    }
    selectedDetailRetry.current = null
  }, [selectedReadError, search.preview, selectedDetailReady])
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
      advancePresentationSettled(
        presentation,
        searchIdentity,
        locationRevision,
        selected?.destination,
      )
    )
      releaseAdvancePresentation()
  }, [locationRevision, releaseAdvancePresentation, searchIdentity, selected?.destination])
  useEffect(() => releaseAdvancePresentation, [releaseAdvancePresentation])
  useEffect(() => {
    const rail = fieldRail.current
    const active = activeFieldButton.current
    if (!rail || !active) return
    centreFieldRail(rail, active)
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
  }, [centreFieldRail, selected?.destination])
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

  const selectCase = (
    reviewCaseId: string,
    field?: string,
    attentionOrigin?: { cursor: string | undefined },
  ) => {
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
        delete next.preparation
        if (attentionOrigin) {
          if (attentionOrigin.cursor) next.attentionCursor = attentionOrigin.cursor
          else delete next.attentionCursor
        }
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
        delete next.preparation
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
    authoredDescription?: DecisionCommand['authoredDescription'],
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
      ...(authoredDescription ? { authoredDescription } : {}),
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
        delete next.preparation
        return next
      },
    })
  }
  const selectFromQueue = (
    reviewCaseId: string,
    field: string,
    attentionCursor: string | undefined,
  ) => {
    workSelectionGeneration.current += 1
    if (reviewCaseId !== selectedReviewCaseId)
      setExpandedChoiceState({ work: reviewCaseId, keys: new Set() })
    setComplete(null)
    setCompletionInspection(null)
    setOpenedWork(null)
    void navigate({
      search: (current) => {
        const next = { ...current, preview: reviewCaseId, field }
        if (attentionCursor) next.attentionCursor = attentionCursor
        else delete next.attentionCursor
        delete next.work
        delete next.attention
        delete next.preparation
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
    (selected.humanDecision !== null ||
      selected.automaticDecision !== null ||
      Boolean(
        selected.descriptionAdvice &&
        (selected.descriptionAdvice.selected_advice?.current === true ||
          selected.descriptionAdvice.application_outcome !== null) &&
        selected.descriptionAdvice.selection,
      ))
  const selectedActionable = selected !== undefined && actionable(selected)
  const selectedRevisitable =
    selected !== undefined &&
    selectedRecorded &&
    selected.editable &&
    selected.decisionActions.length > 0
  const selectedAdvice = selected?.destination === 'description' ? selected.descriptionAdvice : null
  const selectedChoiceMode = selectedActionable || selectedRevisitable || Boolean(selectedAdvice)
  const selectedCoverPreparation =
    selected?.destination === 'cover' &&
    selected.coverConsideration?.current !== true &&
    selected.needsAttention
  const selectedCoverInspection =
    selected?.destination === 'cover' &&
    (selected.coverConsideration !== null || selectedCoverPreparation)
  const selectedProgress = selected ? controller.phase(selected.destination) : null
  // Attention membership comes from the authoritative exact case. A pending
  // or failed local command has not yet changed it.
  const remainingFields = attentionFields.length
  const railPosition =
    complete?.id === selectedReviewCaseId && completionInspection === null
      ? `${String(complete.fields.length)} recorded · work complete`
      : selected
        ? `${String(remainingFields)} remaining · field ${String(
            renderedRailFields.indexOf(selected) + 1,
          )} of ${String(renderedRailFields.length)}`
        : `${String(remainingFields)} remaining · no field selected`
  // The one-tap teaching line is for someone who has not yet seen what a tap
  // does. It says so on the first field only, and gives way to what is actually
  // happening once they have chosen.
  const teachingLine =
    selectedProgress?.phase === 'recording'
      ? 'Recording choice…'
      : selectedProgress?.phase === 'confirmed' ||
          (!selectedProgress && selected && authoredOwnerRecorded(selected))
        ? 'Choice recorded.'
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
          {presentation ? (
            <PreparationSummary
              presentation={presentation}
              reconnecting={preparationState.reconnecting}
              selected={preparationSelected}
              onSelect={() =>
                void navigate({
                  search: (current) => {
                    const next = { ...current, preparation: presentation.run_id }
                    delete next.attention
                    return next
                  },
                })
              }
            />
          ) : null}
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
                  ...(search.preparation ? { preparation: search.preparation } : {}),
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
              data.works.map((work, index) => {
                const selectedWorkRow =
                  !preparationSelected &&
                  (work.reviewCaseId
                    ? search.preview === work.reviewCaseId
                    : search.work === work.workId)
                const needsAttention = work.attentionCount > 0
                // A published report is not a failure. The row for a work
                // that stopped before producing one has nothing to open and
                // stays an article; a work whose evidence is addressable is a
                // successful entry the owner can read.
                const evidence = reportEvidenceUrl(work) !== null
                // Retained evidence is not by itself a clean outcome. A
                // completed preparation reports no terminal failure at all, so
                // its absence is what makes the recorded report the outcome; a
                // later real failure over the same retained report is still a
                // failure the owner has to see.
                const recorded = evidence && work.preparation === null
                const status = recorded
                  ? 'Recorded evidence available'
                  : work.preparation
                    ? 'Preparation failed safely'
                    : needsAttention
                      ? work.attentionCount === 1
                        ? '1 field needs attention'
                        : `${String(work.attentionCount)} fields need attention`
                      : 'No attention needed'
                const terminalFailure = work.preparation !== null && !work.reviewCaseId && !evidence
                const group = providerGroup(work)
                const previousWork = data.works[index - 1]
                const showGroup =
                  search.lens !== 'no_attention_needed' &&
                  (!previousWork || providerGroup(previousWork) !== group)
                const summary = providerSummary(work.preparationOutcomes)
                const summaryId = summary ? `provider-summary-${work.id}` : undefined
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
                      {summary ? (
                        <span id={summaryId} className="library-provider-summary">
                          {summary}
                        </span>
                      ) : null}
                      <span className="library-work-status">
                        <span
                          className={
                            !recorded && (needsAttention || work.preparation)
                              ? 'needs-attention'
                              : 'handled'
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
                return (
                  <Fragment key={work.id}>
                    {showGroup ? (
                      <div className="library-provider-group">
                        <h3>{group}</h3>
                        {group !== 'No attention needed' ? (
                          <p>
                            {group === 'Provider evidence complete'
                              ? 'Lookups completed or did not apply; a match is not guaranteed.'
                              : 'Some lookups did not supply usable evidence. Recoverable lookups can change this group when new evidence arrives.'}
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                    {terminalFailure ? (
                      <article
                        key={work.id}
                        className="library-work-row is-terminal"
                        aria-label="Terminal preparation failure"
                      >
                        {row}
                        {preparation?.mediaHealth.length ? (
                          <MediaHealth observations={preparation.mediaHealth} />
                        ) : null}
                      </article>
                    ) : (
                      <>
                        <button
                          key={work.id}
                          type="button"
                          className={`library-work-row${selectedWorkRow ? ' active' : ''}`}
                          aria-current={selectedWorkRow ? 'true' : undefined}
                          aria-label={`${work.title}. ${authorLine(work)}. ${status}`}
                          aria-describedby={
                            [preparationId, summaryId].filter(Boolean).join(' ') || undefined
                          }
                          onClick={() => void select(work)}
                        >
                          {row}
                        </button>
                        {/* A work whose latest preparation could not read a file
                            still opens what it retained, so the row is a control
                            and the section has to sit beside it rather than
                            inside it: a section nested in a button is not an
                            openable row, and the observation is the one thing
                            the failed attempt proved. */}
                        {preparation?.mediaHealth.length ? (
                          <div className="library-work-row-aside">
                            <MediaHealth observations={preparation.mediaHealth} />
                          </div>
                        ) : null}
                      </>
                    )}
                  </Fragment>
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
          (!displayedIdentity || selectedReadPending || selectedReadError)
            ? { 'data-route-focus': 'true' }
            : {})}
        >
          {preparationSelected ? (
            <PreparationDetail
              presentation={presentation}
              reconnecting={preparationState.reconnecting}
              animateChange={animatedPresentationVersion === presentationVersion}
              dismissing={dismissPreparation.isPending}
              onDismiss={() =>
                dismissPreparation.mutate({
                  runId: presentation.run_id,
                  navigationGeneration: routeNavigationGeneration.current,
                  locationRevision,
                  searchIdentity,
                })
              }
            />
          ) : selectedWork ? (
            <WorkSummary work={selectedWork} />
          ) : !search.preview ? (
            <div className="library-selection">
              <h2>Choose a work</h2>
              <p>
                Its current values and field position stay here while you move through attention.
              </p>
            </div>
          ) : !displayedIdentity ? (
            identityPending ? (
              <p role="status">Loading selected work…</p>
            ) : identityError ? (
              <p role="alert">
                Selected work could not be loaded.{' '}
                <button
                  ref={selectedDetailRetryButton}
                  onClick={() => {
                    selectedDetailRetry.current = search.preview ?? null
                    void attention.refetch()
                  }}
                >
                  Try again
                </button>
              </p>
            ) : (
              <div className="library-selection">
                <h2>Selected work</h2>
                <p>Readable work identity is not available on this Library page.</p>
                <p role="status">This work is not available in the current Library result.</p>
              </div>
            )
          ) : detail.isPending ? (
            <p role="status">Loading selected work…</p>
          ) : detail.isError && staleCoverNotice ? (
            staleCoverNotice
          ) : detail.isError ? (
            <p role="alert">
              {controller.messageFor(search.field ?? selected?.destination ?? '') ??
                'Selected work could not be loaded.'}{' '}
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
              {staleCoverNotice ?? (
                <p role="alert">
                  This review case is no longer current. Reopen its current replacement from
                  Library.
                </p>
              )}
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
                        detail.data.fields.filter(
                          (field) =>
                            field.humanDecision === null && field.automaticDecision !== null,
                        ).length,
                      )}
                    </dd>
                  </div>
                </dl>
                <nav className="selected-work-diagnostics" aria-label="Read-only work diagnostics">
                  <Link
                    params={{ reviewCaseId: detail.data.id }}
                    search={normalizeDetailSearch({
                      ...search,
                      subview: 'evidence',
                      field: selected?.destination,
                    })}
                    to="/review/$reviewCaseId"
                  >
                    Evidence
                  </Link>
                  <Link
                    params={{ reviewCaseId: detail.data.id }}
                    search={normalizeDetailSearch({
                      ...search,
                      subview: 'history',
                      field: selected?.destination,
                    })}
                    to="/review/$reviewCaseId"
                  >
                    History
                  </Link>
                </nav>
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
                  {renderedRailFields.map((field) => (
                    <button
                      key={field.destination}
                      ref={
                        revealFocusDestination === field.destination
                          ? (button) => {
                              if (button) {
                                setRevealFocusDestination(null)
                                button.focus()
                                const rail = fieldRail.current
                                if (rail) centreFieldRail(rail, button)
                              }
                            }
                          : selected?.destination === field.destination
                            ? activeFieldButton
                            : undefined
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
                            : field.humanDecision || authoredOwnerRecorded(field)
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
                  {omittedFields.length ? (
                    <button
                      type="button"
                      onClick={() => {
                        setRevealFocusDestination(omittedFields[0]?.destination ?? null)
                        setShownEmptyFields((current) => ({
                          ...current,
                          [selectedReviewCaseId]: true,
                        }))
                      }}
                    >
                      {`Show ${String(omittedFields.length)} empty field${omittedFields.length === 1 ? '' : 's'}`}
                    </button>
                  ) : null}
                </nav>
              </section>
              {staleCoverNotice}
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
                          selectCase(nextWork.id, nextWork.destination, {
                            cursor: nextWork.attentionCursor,
                          })
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
                        <>
                          <p>{`${fieldLabel(selected.destination)} · ${fieldReason(selected)}`}</p>
                          {selected.automaticWithheldReason ? (
                            <p className="automatic-withheld-reason">
                              {selected.automaticWithheldReason}
                            </p>
                          ) : null}
                        </>
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
                  {(selectedChoiceMode || selectedCoverInspection) && !controller.reloadRequired ? (
                    selected.destination === 'cover' ? (
                      <LibraryCoverChoices
                        reviewCaseId={selectedReviewCaseId}
                        mediaKind={detail.data.target.mediaKind}
                        field={selected}
                        disabled={!selectedChoiceMode && !selectedCoverPreparation}
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
                        {selectedRevisitable ? (
                          <>
                            <ProviderConsensusAutomaticStatement field={selected} />
                            <DescriptionAutomaticStatement field={selected} />
                            <SeriesAutomaticStatement field={selected} />
                            <VerifiedManifestationIsbnAutomaticStatement field={selected} />
                          </>
                        ) : null}
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
                          {(selected.decisionActions.includes('accept') &&
                            !selected.preservationOnly) ||
                          selectedAdvice?.candidate ||
                          selectedAdvice?.selected_advice ? (
                            <div
                              className={`proposed-choice-list${selected.proposedValues.length + (selectedAdvice?.candidate ? 1 : 0) === 1 ? ' has-one-choice' : ''}`}
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
                              {(selected.decisionActions.includes('accept') &&
                              !selected.preservationOnly
                                ? selected.proposedValues
                                : []
                              ).map((value, index) => {
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
                              {selectedAdvice?.selected_advice &&
                              selectedAdvice.selected_advice.candidate.candidate_id !==
                                selectedAdvice.candidate?.candidate_id ? (
                                <MetadataChoice
                                  advice={selectedAdvice}
                                  retainedAdvice
                                  context={undefined}
                                  current={false}
                                  disabled
                                  expanded={false}
                                  label="Advised · Authored content · Recorded choice"
                                  longDescription={isLongDescription(
                                    'description',
                                    selectedAdvice.selected_advice.candidate.output.description,
                                  )}
                                  onChoose={() => undefined}
                                  recording={false}
                                  selected={Boolean(
                                    selectedAdvice.selected_advice.current ||
                                    selectedAdvice.application_outcome,
                                  )}
                                  toggle={() => undefined}
                                  value={
                                    selectedAdvice.selected_advice.candidate.output.description
                                  }
                                />
                              ) : null}
                              {selectedAdvice?.candidate ? (
                                <MetadataChoice
                                  advice={selectedAdvice}
                                  retainedAdvice={
                                    selectedAdvice.selection?.candidate_id ===
                                    selectedAdvice.candidate.candidate_id
                                  }
                                  context={undefined}
                                  current={false}
                                  disabled={
                                    !selected.editable ||
                                    !selectedAdvice.current ||
                                    controller.recording ||
                                    selectedAdvice.state === 'requested' ||
                                    selectedAdvice.state === 'running'
                                  }
                                  expanded={false}
                                  label="Advised · Authored content"
                                  longDescription={isLongDescription(
                                    'description',
                                    selectedAdvice.candidate.output.description,
                                  )}
                                  onChoose={() => {
                                    if (
                                      !selectedAdvice.current ||
                                      !selectedAdvice.candidate ||
                                      !selected.editable
                                    )
                                      return
                                    choose(
                                      selected,
                                      'accept',
                                      {
                                        kind: 'text',
                                        value: selectedAdvice.candidate.output.description,
                                      },
                                      {
                                        candidateId: selectedAdvice.candidate.candidate_id,
                                        binding: selectedAdvice.binding,
                                      },
                                    )
                                  }}
                                  recording={
                                    selectedProgress?.command.authoredDescription?.candidateId ===
                                      selectedAdvice.candidate.candidate_id &&
                                    selectedProgress.phase === 'recording'
                                  }
                                  selected={
                                    selectedProgress && selectedProgress.phase !== 'failed'
                                      ? selectedProgress.command.authoredDescription
                                          ?.candidateId === selectedAdvice.candidate.candidate_id
                                      : Boolean(
                                          (selectedAdvice.selected_advice?.current === true ||
                                            selectedAdvice.application_outcome) &&
                                          selectedAdvice.selection?.candidate_id ===
                                            selectedAdvice.candidate.candidate_id,
                                        )
                                  }
                                  toggle={() => undefined}
                                  value={selectedAdvice.candidate.output.description}
                                />
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                        {selectedAdvice ? (
                          <DescriptionAdvicePreparation
                            advice={selectedAdvice}
                            reviewCaseId={selectedReviewCaseId}
                            disabled={controller.recording || controller.reloadRequired}
                          />
                        ) : null}
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
                    onClick={() => selectFromQueue(item.id, item.destination, item.attentionCursor)}
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
