import {
  invalidateBackgroundQueries,
  prefetchBackgroundQuery,
  runRequiredRead,
  useRequiredRead,
  queryWorkClass,
  useRequiredQuery as useQuery,
} from '../queryWork'
import { RecordingIdentityBasis } from '../components/RecordingIdentityBasis'
import { DescriptionAdvicePreparation } from '../components/DescriptionAdvice'
import { MediaHealth } from '../components/MediaHealth'
import { CountPhrases } from '../components/CountPhrases'
import { useInfiniteQuery, useMutation } from '@tanstack/react-query'
import { Link, createLink, useNavigate, useRouter, useRouterState } from '@tanstack/react-router'
import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { ComponentPropsWithRef, KeyboardEvent, RefObject, SyntheticEvent } from 'react'
import type { QueryExecuteOptions, QueryKey } from '@tanstack/react-query'

import { ApiError, CsrfRenewedError, displayValue, sessionRefreshedCopy } from '../api/http'
import type {
  AutomaticOutcome,
  DecisionCommand,
  DecisionOutcome,
  LibraryLens,
  LibrarySearch,
  MediaKind,
  LibraryView,
  LibraryCoverView,
  LibraryWorkView,
  PreparationView,
  ProviderOutcomeView,
  ProposalOutcome,
  TargetDestination,
  WorkSummaryState,
} from '../api/http'
import { choiceRadios, moveChoiceFocus } from '../choiceGroup'
import { useRouteReadyFocus } from '../focus'
import {
  audioSurfaceLabels,
  claimBasisLabels,
  claimKindLabels,
  destinationLabel,
  fieldStateLabels,
  languageName,
  lookupLabels,
  materialReasonLabel,
  mediaKindLabels,
  opaqueSource,
  preparationOutcomes,
  preparationStageLabels,
  preparationStatusLabels,
  sourceStatusLabels,
} from '../labels'
import { coalesceMilliseconds } from '../liveInvalidation'
import {
  detailQuery,
  libraryQuery,
  libraryRoute,
  librarySummaryQuery,
  PreparationNotice,
  ProviderThrottleNotice,
  usePreparation,
  useReviewContext,
  normalizeDetailSearch,
} from '../router'
import { WriteReadyNotice } from '../components/WriteReadyNotice'
import { Button } from '../components/Button'
import { StatusPill } from '../components/StatusPill'
import {
  actionable,
  choiceAvailable,
  requestKey,
  useReviewDecisionController,
  writtenRefusal,
} from '../reviewDecisionController'
import { emptyUnproposed, ownerChoiceCount, primaryField, railOffers } from '../reviewChoiceCounts'
import { advancePresentationSettled } from '../reviewAdvancePresentation'
import { formatRunningTime } from '../dates'

import {
  shownAdvice,
  coverNames,
  effectSummary,
  sameSelectedValue,
  commandSelected,
  choiceSelected,
  isLongDescription,
  decisionQuestion,
  audioFileFormat,
} from '../fieldDecision'
import type { ReviewField } from '../fieldDecision'
import {
  ChoiceEvidencePanel,
  DescriptionText,
  RecordedFieldObservations,
  MetadataChoice,
} from './FieldDecisionChoices'
import { LibraryCoverChoices } from './FieldDecisionCover'
const authoredOwnerRecorded = (field: ReviewField) =>
  field.descriptionAdvice?.selection?.origin === 'human' &&
  Boolean(
    field.descriptionAdvice.selected_advice?.current === true ||
    field.descriptionAdvice.application_outcome,
  )
const searchNavigationDelay = 150
const noWorks: readonly LibraryWorkView[] = []
// A pointer crossing several rows warms only the one it rests on.
const prefetchIntentDelay = 50
// Live Library invalidation batches the documented fifty-work burst for 120 ms.
// Keep each distinct subject from that bounded batch long enough to compare it
// with the next bounded page read, but never treat a frame as selection
// authority.
const maxCoalescedSuccessorCandidates = 50
const librarySearchIdentity = (search: object) =>
  JSON.stringify(Object.entries(search).sort(([left], [right]) => left.localeCompare(right)))
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
// A claim's role is the source's own words, shown as written; a role the
// source supplied as unsafe or oversized text reads as such, never as its digest.
// A language value reads by its name beside its code, `English (en)` (#1286).
const valueText = (destination: TargetDestination, value: string) =>
  destination === 'language' ? languageName(value) : value
const claimRole = (role: string) =>
  /^(?:unsafe|oversized)-source-text-sha256:/u.test(role) ? 'role not shown' : role
const lensLabels = {
  all: 'All',
  needs_attention: 'Needs attention',
  no_attention_needed: 'No attention needed',
  not_matched: 'Not matched',
  preparation_failed: 'Could not finish',
} satisfies Record<LibraryLens, string>
// The masthead's Library link is the current page; the active lens is the
// current item within it, so it says `true` rather than a second `page` (#1530).
const LensLink = createLink((props: ComponentPropsWithRef<'a'>) => (
  <a {...props} aria-current={props['aria-current'] ? 'true' : undefined} />
))
// The first letter of each of the first two words that have one, so a
// bracket or an ampersand never stands for a word (#1290).
const initials = (title: string) =>
  title
    .split(/\s+/u)
    .map((word) => /\p{L}/u.exec(word)?.[0])
    .filter((letter): letter is string => letter !== undefined)
    .slice(0, 2)
    .map((letter) => letter.toUpperCase())
    .join('') || 'ML'
// A work's mark: the cover the library holds when its report retained one,
// contained whole and never setting the row's height, else its spine of
// initials. A cover that fails to load falls back to the spine (#1290).
function BookMark({ title, cover }: { title: string; cover: LibraryCoverView | undefined }) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const shown = cover !== undefined && failedUrl !== cover.url
  return (
    <span
      className={shown ? 'library-book-mark has-cover' : 'library-book-mark'}
      aria-hidden="true"
    >
      {shown ? (
        <img
          src={cover.url}
          alt=""
          width={cover.width}
          height={cover.height}
          loading="lazy"
          decoding="async"
          onError={() => {
            setFailedUrl(cover.url)
          }}
        />
      ) : (
        initials(title)
      )}
    </span>
  )
}
type PreparationPresentation = NonNullable<PreparationView['presentation']>
// The run's works as they stand now, in the masthead's words (#1366). The
// latched passage is first-finish history and is labelled Prepared instead.
const runOutcomes = (presentation: PreparationPresentation) =>
  preparationOutcomes(presentation.now ?? null)
// What the run is doing now, said in the counts the owner is waiting on rather
// than in the store's state name. A terminal run states its own outcome here
// because that is the line a reader looks at first.
const preparationStateLine = (presentation: PreparationPresentation) => {
  switch (presentation.state) {
    case 'starting':
      return 'Preparation is starting'
    case 'running':
      return 'Preparation continues'
    case 'blocked':
      return 'Paused until the reported connection issue is resolved'
    case 'completed_clean':
    case 'completed_with_failures':
      return (
        <>
          Finished · <CountPhrases phrases={runOutcomes(presentation)} />
        </>
      )
    case 'dismissed':
      return 'Preparation dismissed'
  }
}
const preparationTriggerCopy: Record<
  PreparationPresentation['triggers'][number]['trigger'],
  string
> = {
  initial_setup: 'Started by initial setup',
  manual_scan: 'Started by an owner scan',
  library_added: 'Started when a library was added',
  title_recovery: 'Title recovery requested',
}
// A request accepted once is just how the run began; a number there would be
// noise on every ordinary run. Repeats are the case worth counting, so only
// they carry one.
// Initial setup records one acceptance per library, which is not a repeated
// owner request, so it never carries a count.
const preparationRepeatedTriggerCopy: Record<
  Exclude<PreparationPresentation['triggers'][number]['trigger'], 'initial_setup'>,
  string
> = {
  manual_scan: 'Owner scan requested',
  library_added: 'Library addition requested',
  title_recovery: 'Title recovery requested',
}
const preparationTriggerLine = (trigger: PreparationPresentation['triggers'][number]) =>
  trigger.accepted_count > 1 && trigger.trigger !== 'initial_setup'
    ? `${preparationRepeatedTriggerCopy[trigger.trigger]} ${String(trigger.accepted_count)} times`
    : preparationTriggerCopy[trigger.trigger]
// The scope every count on this page reconciles to: one run, whose books are
// either ready, unable to finish, or still moving.
/**
 * The display name, bounded to what the search route and the backend accept.
 *
 * The two bounds count differently: the route's ``bounded`` (router.tsx) tests
 * ``value.length``, which is UTF-16 units, while the backend's `max_length=128`
 * counts characters. At most 128 units therefore satisfies both, and bounding
 * by characters does not -- 127 ASCII plus one astral character is 128
 * characters but 129 units, which the route drops entirely. So the slice is by
 * units, and a trailing high surrogate left behind by it is dropped rather than
 * sent as half a character.
 */
const boundedSearchName = (name: string) => {
  const bounded = name.slice(0, 128)
  const last = bounded.charCodeAt(bounded.length - 1)
  return last >= 0xd800 && last <= 0xdbff ? bounded.slice(0, -1) : bounded
}

// A separator never starts a line (#1013).
const preparationScope = (presentation: PreparationPresentation, recoveryRequired: boolean) => (
  <CountPhrases
    phrases={[
      ...runOutcomes(presentation),
      `${presentation.now ? String(presentation.now.in_progress) : '…'} ${recoveryRequired ? 'retained' : 'in progress'}`,
    ]}
    separator={'\u00a0· '}
  />
)
const preparationPassage = [
  ['read', 'Read from library'],
  ['searches_finished', 'Searches finished'],
  ['compared', 'Candidates compared'],
  ['checked', 'Metadata checked'],
] as const

// The compact layout, where the selected work replaces the list.
const compactWidth = () =>
  typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 1024px)').matches
const narrowWidth = () =>
  typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 760px)').matches
const workCount = (count: number) => `${String(count)} ${count === 1 ? 'work' : 'works'}`
// A search under a lens counts that lens against its whole-library total; the
// lens link names it visually and the status names it to screen readers (#1425).
const lensMatching = {
  all: ['all', undefined],
  needs_attention: ['attention', 'needing attention'],
  no_attention_needed: ['clear', 'needing no attention'],
  not_matched: ['notMatched', 'not matched'],
  preparation_failed: ['failed', 'that could not finish'],
} as const

const PreparationSummary = ({
  presentation,
  reconnecting,
  recoveryRequired,
  selected,
  onSelect,
}: {
  presentation: PreparationPresentation
  reconnecting: boolean
  recoveryRequired: boolean
  selected: boolean
  onSelect: () => void
}) => {
  const active = presentation.active_books.length + presentation.active_overflow
  let title: string
  // What follows the run's outcomes on the summary line.
  let rest: string
  switch (presentation.state) {
    case 'starting':
      title = 'Preparation is starting'
      rest = ` · ${String(active)} active`
      break
    case 'running':
      title = 'Preparing your library'
      rest = ` · ${String(active)} active`
      break
    case 'blocked':
      title = 'Preparation needs attention'
      rest = ` · ${String(active)} active`
      break
    case 'completed_clean':
      title = 'Preparation complete'
      rest = ''
      break
    case 'completed_with_failures':
      title = 'Preparation completed with failures'
      rest = ''
      break
    case 'dismissed':
      title = 'Preparation dismissed'
      rest = ''
      break
  }
  if (recoveryRequired) {
    title = 'Preparation stopped'
    rest = ' · Service restart required'
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
        <small>
          {reconnecting && !recoveryRequired ? (
            'Reconnecting · last confirmed progress shown'
          ) : (
            <>
              This run: <CountPhrases phrases={runOutcomes(presentation)} />
              {rest}
            </>
          )}
        </small>
      </span>
    </button>
  )
}

const PreparationDetail = ({
  presentation,
  reconnecting,
  recoveryRequired,
  animateChange,
  dismissing,
  onDismiss,
}: {
  presentation: PreparationPresentation
  reconnecting: boolean
  recoveryRequired: boolean
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
      introduction = 'Prepared works remain selectable while the rest of this run finishes.'
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
        'This run finished with reported failures; prepared books remain available and the failed outcomes stay visible.'
      break
    case 'dismissed':
      heading = 'Preparation dismissed'
      introduction = 'This preparation record has been dismissed.'
      break
  }
  if (recoveryRequired) {
    heading = 'Preparation stopped'
    introduction =
      'Restart the Mechanical Librarian service to resume preparation. Refreshing this page or scanning again cannot recover it. Prepared books remain available.'
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
        <strong>
          {presentation.now ? String(presentation.now.ready) : '…'} ready to browse in this run
        </strong>
      </header>
      <p
        className={`preparation-run-state state-${presentation.state}${animateChange ? ' reported-change' : ''}`}
        role="status"
      >
        {recoveryRequired
          ? 'Service restart required · last saved progress shown'
          : reconnecting
            ? 'Reconnecting · showing the last confirmed state'
            : preparationStateLine(presentation)}
      </p>
      <p className="preparation-provenance">
        {presentation.triggers.map(preparationTriggerLine).join(' · ')}
      </p>
      <section aria-labelledby="now-preparing-title">
        <div className="preparation-section-heading">
          <h3 id="now-preparing-title">{recoveryRequired ? 'Retained work' : 'Now preparing'}</h3>
          <span>
            {String(presentation.active_books.length + presentation.active_overflow)}{' '}
            {recoveryRequired ? 'retained' : 'active'}
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
          <p>
            {recoveryRequired
              ? 'No work is retained at the last confirmed refresh.'
              : 'No book is active at the last confirmed refresh.'}
          </p>
        )}
        {presentation.active_overflow ? (
          <p>
            {String(presentation.active_overflow)} more {recoveryRequired ? 'retained' : 'active'}{' '}
            books follow in this run.
          </p>
        ) : null}
        {presentation.active_books.length ? (
          <small className="activity-backup-note">
            A book appears in Works once it is prepared.
          </small>
        ) : null}
      </section>
      <section aria-labelledby="preparation-progress-title">
        <div className="preparation-section-heading">
          <div>
            <h3 id="preparation-progress-title">Preparation progress</h3>
            <p>
              Each stage counts the books that first reached it in this run, and a book that could
              not finish stops at its last stage; In this run counts them as they stand now.
            </p>
          </div>
          {/*
           * A rate is only a fact while the run is moving. A finished, paused
           * or dismissed run has no current rate, and a reconnected page has
           * no current anything -- printing 0.0 books/min there read as a
           * performance fault rather than as silence.
           */}
          {!recoveryRequired &&
          !reconnecting &&
          (presentation.state === 'starting' || presentation.state === 'running') ? (
            <strong>
              {presentation.throughput_per_minute > 0
                ? `${presentation.throughput_per_minute.toFixed(1)} books/min over the last five minutes`
                : 'No book finished in the last five minutes'}
            </strong>
          ) : null}
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
                <strong>
                  {String(presentation.passage[field])} of {String(presentation.total)}
                </strong>
              </dd>
            </div>
          ))}
          <div className="preparation-outcomes">
            <dt>In this run</dt>
            <dd>
              <strong>{preparationScope(presentation, recoveryRequired)}</strong>
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
                <span>
                  {completion.name}{' '}
                  {/*
                   * The link sits inside the name so it wraps with it, and the
                   * name shown is the only thing this row has to search with.
                   */}
                  <Link
                    to="/library"
                    search={{ lens: 'all', q: boundedSearchName(completion.name) }}
                  >
                    Find in Works
                  </Link>
                </span>
                <strong>
                  {completion.outcome === 'ready' ? 'Prepared' : 'Stopped before finishing'}
                </strong>
              </li>
            ))}
          </ul>
        ) : (
          <p>No terminal book has been reported yet.</p>
        )}
        {presentation.passage.failed > 0 ? (
          <small className="activity-backup-note">
            A book that could not finish is listed in Works under its library title, with its stage,
            reason, next step and reference. If its library record has since changed, it is listed
            as Preparation unavailable.
          </small>
        ) : null}
      </section>
      {presentation.dismissible ? (
        <button type="button" disabled={recoveryRequired || dismissing} onClick={onDismiss}>
          {dismissing ? 'Dismissing…' : 'Dismiss preparation'}
        </button>
      ) : null}
    </div>
  )
}
const bounded = (value: string, fallback: string) =>
  opaqueSource.test(value) ? fallback : value.length > 160 ? `${value.slice(0, 157)}…` : value
const decisionValue = (field: ReviewField) => {
  if (authoredOwnerRecorded(field) && field.descriptionAdvice?.selected_advice)
    return field.descriptionAdvice.selected_advice.candidate.output.description
  // A hold carried from an earlier case keeps the library's value (ADR-0085).
  const selected = (field.humanDecision ?? (field.manualHold ? null : field.automaticDecision))
    ?.selected_value
  if (field.destination === 'cover') {
    const candidates = field.coverConsideration?.candidates ?? []
    const candidate = candidates.find((item) => item.artifactId === selected?.value)
    return candidate?.incumbent || !selected
      ? 'Current cover'
      : candidate
        ? (coverNames(candidates).get(candidate.reference) ?? 'Selected cover')
        : 'Selected cover'
  }
  return valueText(
    field.destination,
    selected === null || selected === undefined ? field.currentValue : displayValue(selected.value),
  )
}
// A recording's settled fill stays choosable without asking for review (#1249).
const optionalChoice = (field: ReviewField) =>
  field.classification === 'owner_choosable' && choiceAvailable(field)
// A field tab says where its field stands in words, never a glyph: an
// undecided optional field and a decided one both drew a check (#1290).
const fieldStatus = (field: ReviewField) =>
  actionable(field)
    ? (['needs', 'Needs you'] as const)
    : field.humanDecision || authoredOwnerRecorded(field) || field.manualHold
      ? (['decided', 'Decided'] as const)
      : field.classification === 'automatic_recorded' || field.automaticDecision
        ? (['automatic', 'Automatic'] as const)
        : optionalChoice(field)
          ? (['optional', 'Optional'] as const)
          : (['unchanged', 'No change'] as const)
// A field with nothing to decide is either unchanged, because no source
// offered a different value, or held: unavailable to this service, or waiting
// on a recovery. The server's reason is the only thing that tells them apart,
// so the two reasons for an unchanged field are named here (#1514).
const unchangedReasons = new Set([
  '',
  'There is nothing to decide for this field.',
  'There is no other value to choose from. You can enter your own.',
])
const contextKind = (field: ReviewField) =>
  field.manualHold || !unchangedReasons.has(field.classificationReason)
    ? /waiting for|pending a/u.test(field.classificationReason)
      ? ('waiting' as const)
      : ('unavailable' as const)
    : ('unchanged' as const)
const heldPhrase = (count: number, kind: 'waiting' | 'unavailable') =>
  `${String(count)} ${count === 1 ? 'field' : 'fields'} ${kind === 'waiting' ? 'waiting for a recovery' : 'unavailable'}`
// Why a field reads as it does when the server gives no reason of its own.
const humanReasons = {
  accept: 'You chose a proposed value for this field.',
  keep_current: 'You kept the current value for this field.',
  reject: 'You declined the proposed change for this field.',
  owner_entered: 'You entered a value for this field.',
  withdraw: 'You withdrew your decision for this field.',
} satisfies Record<DecisionOutcome, string>
const automaticReasons = {
  candidate: 'Automatic policy chose a proposed value.',
  keep_current: 'Automatic policy kept the current value.',
  ignore: 'Automatic policy leaves this field unchanged.',
} satisfies Record<AutomaticOutcome, string>
const dispositionReasons = {
  reject: 'The proposed change was set aside; compare the values yourself.',
  propose_change: 'A change is proposed; compare it with the current value.',
  acceptable_choice: 'More than one value is acceptable; choose the one this work should use.',
  unresolved_choice: 'The sources do not settle one value; compare the values yourself.',
  keep: 'The current value is proposed to stay; compare the alternatives.',
  defer: 'This field waits for your comparison of the values.',
  ineligible: 'None of these values can be applied for you; compare them yourself.',
} satisfies Record<ProposalOutcome, string>
const fieldReason = (field: ReviewField) =>
  bounded(
    field.humanDecision
      ? field.classificationReason || humanReasons[field.humanDecision.outcome]
      : field.automaticDecision
        ? field.classificationReason || automaticReasons[field.automaticDecision.outcome]
        : actionable(field)
          ? field.classificationReason || dispositionReasons[field.disposition]
          : field.classificationReason &&
              !/needs (?:your )?review/iu.test(field.classificationReason)
            ? field.classificationReason
            : 'No eligible owner decision is available.',
    'Retained reviewer notice',
  )
const ownerOutcome = (outcome: DecisionCommand['outcome']) =>
  outcome === 'owner_entered' || outcome === 'withdraw'
const completionOutcome = (field: ReviewField) =>
  authoredOwnerRecorded(field)
    ? 'Advised description selected'
    : field.humanDecision?.outcome === 'reject'
      ? 'No action recorded'
      : field.humanDecision?.outcome === 'keep_current'
        ? 'Current value retained'
        : field.humanDecision?.outcome === 'accept'
          ? 'Proposed value selected'
          : 'Recorded outcome'
const choiceContext = (field: ReviewField, alternativeId: string | null) =>
  field.choiceContexts.find((context) => context.alternativeId === alternativeId)
const comparisonNotices = (field: ReviewField) =>
  field.choiceContexts.some((context) =>
    context.limitations.some((limitation) => materialReasonLabel(limitation) !== undefined),
  )
    ? []
    : field.notices.filter(
        (notice) => materialReasonLabel(notice) !== undefined && !restatesReason(field, notice),
      )
// A comparison condition in words; differing source tag lists or synopses are
// named as such rather than as a disagreement.
const noticeText = (field: ReviewField, notice: string, mediaKind: string) =>
  notice === 'competing_evidence' && combinedAutomaticTags(field, mediaKind)
    ? 'Different source tag lists'
    : notice === 'competing_evidence' && selectedAutomaticSynopsis(field, mediaKind)
      ? 'Different source descriptions'
      : materialReasonLabel(notice)
// A notice that says again what the reason under the question already says is
// left out: the language respelling reason, or the condition that is that
// line (#1519).
const restatesReason = (field: ReviewField, notice: string) =>
  notice === questionNotice(field) ||
  (field.automaticWithheldReason !== null &&
    (notice === 'language_display_name' ||
      `${materialReasonLabel(notice) ?? ''}.` === field.automaticWithheldReason))
// Under an actionable field's question, one line gives its own reason: why it
// was withheld, else its first comparison condition, else its disposition. The
// classification sentence, the same on every field, is only the last fallback
// (#1519).
const questionNotice = (field: ReviewField) =>
  actionable(field) && field.automaticWithheldReason === null
    ? field.notices.find((notice) => materialReasonLabel(notice) !== undefined)
    : undefined
const questionReason = (field: ReviewField, mediaKind: string) => {
  const notice = questionNotice(field)
  const label = notice === undefined ? undefined : noticeText(field, notice, mediaKind)
  return (
    field.automaticWithheldReason ??
    (label
      ? `${label}.`
      : ((dispositionReasons as Partial<Record<string, string>>)[field.disposition] ??
        fieldReason(field)))
  )
}

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
// Why no provider matched a work, in the row's status slot (#1407).
const notMatchedReasons: Partial<Record<WorkSummaryState, string>> = {
  not_matched_refused: 'Found, but not this work',
  not_matched_unavailable_scheduled: 'Providers unavailable; will be checked again',
  not_matched_unavailable: 'Providers unavailable',
  not_matched_no_match: 'No provider matched',
}
const providerGroup = (work: LibraryWorkView) =>
  work.preparation
    ? 'Preparation needs attention'
    : work.attention === 'not_matched'
      ? 'Not matched'
      : work.attentionCount === 0
        ? 'No attention needed'
        : work.preparationOutcomes.some((outcome) => limitingProviderStatuses.has(outcome.status))
          ? 'Provider evidence limited'
          : 'Provider evidence complete'
const providerSummary = (outcomes: readonly ProviderOutcomeView[]) => {
  const counts = new Map<string, number>()
  for (const outcome of outcomes) {
    const label = sourceStatusLabels[outcome.status]
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  return [...counts]
    .map(([label, count]) => (count > 1 ? `${String(count)} sources: ${label}` : label))
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
  const ledgerLabel = `${String(outcomes.length)} ${outcomes.length === 1 ? 'lookup recorded' : 'lookups recorded'} for this work`
  return (
    <details className="work-sources">
      <summary>
        {comparison ? (
          <span>{ledgerLabel}</span>
        ) : (
          <>
            <span>Sources</span>
            <small>{ledgerLabel}</small>
          </>
        )}
      </summary>
      <ul>
        {outcomes.map((outcome, index) => (
          <li key={`${outcome.provider}:${outcome.laneKind}:${outcome.status}:${String(index)}`}>
            <strong>{outcome.provider}</strong>
            <b>{sourceStatusLabels[outcome.status]}</b>
            <span>{outcome.reason}</span>
            <small>
              {lookupLabels[outcome.laneKind]} · {String(outcome.providerRequests)}{' '}
              {outcome.providerRequests === 1 ? 'request' : 'requests'}
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
    queryFn: (queryContext) =>
      http.getReportDetail(detailUrl, queryContext.signal, queryWorkClass(queryContext)),
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
      <RecordingIdentityBasis
        value={data.recordingIdentityBasis}
        scope={data.audiobookReviewScope}
        offered={[]}
      />
      {data.evidence.length === 0 ? (
        <p role="status">No local evidence was recorded for this work.</p>
      ) : (
        <dl className="library-work-values">
          {data.evidence.map((claim, index) => (
            <div key={`${claim.kind}-${claim.basis}-${String(index)}`}>
              <dt>
                {claimKindLabels[claim.kind]}
                {claim.role ? ` · ${claimRole(claim.role)}` : ''}
              </dt>
              <dd>
                {/* Only a valid running time reads in hours and minutes; any
                    other claim keeps its recorded text. */}
                {(claim.kind === 'duration' && claim.state === 'valid'
                  ? formatRunningTime(claim.value)
                  : null) ?? claim.value}
                {/* An oversized value is retained as its beginning only, so it
                    says so rather than reading as the whole (#1290). */}
                {claim.state === 'oversized' ? '…' : null}
                <small>
                  {claimBasisLabels[claim.basis]}
                  {claim.state === 'valid' ? '' : ` · ${fieldStateLabels[claim.state]}`}
                  {claim.state === 'oversized' ? ' · Only the beginning is shown' : null}
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
              data.coverSurface ? ` (${audioSurfaceLabels[data.coverSurface]})` : ''
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
        <BookMark title={work.title} cover={work.cover} />
        <div>
          <h2>{work.title}</h2>
          <p>
            {authorLine(work)} · {work.libraryDisplayName ?? work.library} ·{' '}
            {mediaKindLabels[work.mediaKind]}
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
              {preparationStatusLabels[work.preparation.status]}{' '}
              {preparationStageLabels[work.preparation.stage]}
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
              <dt>{destinationLabel(field.destination)}</dt>
              <dd>
                {field.state === 'value' ? valueText(field.destination, field.value) : field.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {evidenceUrl ? <WorkEvidence detailUrl={evidenceUrl} /> : null}
      <WorkSources outcomes={work.preparationOutcomes} />
    </>
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
      aria-label={`${destinationLabel(field.destination)} current value`}
    >
      <dl>
        <div>
          <dt>Current value</dt>
          <dd>
            {field.destination === 'description' ? (
              <DescriptionText key={field.currentValue} value={field.currentValue} />
            ) : (
              valueText(field.destination, field.currentValue)
            )}
          </dd>
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
          label={`${destinationLabel(field.destination)} current value`}
        />
      ) : null}
    </section>
  )
}

function emptyCurrentValue(field: ReviewField) {
  return field.currentRaw == null || (Array.isArray(field.currentRaw) && !field.currentRaw.length)
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
    !emptyCurrentValue(field)
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

function NarratorsAutomaticStatement({ field }: { field: ReviewField }) {
  const providers = field.automaticSelectionSources ?? []
  if (
    field.humanDecision !== null ||
    field.destination !== 'narrators' ||
    field.automaticDecision?.outcome !== 'candidate' ||
    !providers.length ||
    !emptyCurrentValue(field)
  ) {
    return null
  }
  return (
    <p>
      Selected from {providers.join(' and ')} — the same cast in each — to fill an empty Narrators
      field under a qualified automatic policy.
    </p>
  )
}

function AsinAutomaticStatement({ field }: { field: ReviewField }) {
  const providers = field.automaticSelectionSources ?? []
  if (
    field.humanDecision !== null ||
    field.destination !== 'asin' ||
    field.automaticDecision?.outcome !== 'candidate' ||
    !providers.length ||
    !emptyCurrentValue(field)
  ) {
    return null
  }
  return (
    <p>
      Selected from {providers.join(' and ')} to fill an empty ASIN field under a qualified
      automatic policy; the runtime gate corroborated that this recording is that product.
    </p>
  )
}

const combinedAutomaticTags = (field: ReviewField, mediaKind: string) =>
  audioFileFormat(mediaKind) &&
  field.humanDecision === null &&
  field.destination === 'tags' &&
  field.automaticDecision?.outcome === 'candidate' &&
  !field.proposedValues.some((value) =>
    sameSelectedValue(value, field.automaticDecision?.selected_value),
  )

const selectedAutomaticSynopsis = (field: ReviewField, mediaKind: string) =>
  audioFileFormat(mediaKind) &&
  field.destination === 'description' &&
  field.humanDecision === null &&
  field.automaticDecision?.outcome === 'candidate' &&
  field.proposedValues.some((value) =>
    sameSelectedValue(value, field.automaticDecision?.selected_value),
  )

const selectedAutomaticLanguageForm = (field: ReviewField) =>
  field.destination === 'language' &&
  field.humanDecision === null &&
  field.automaticDecision?.outcome === 'candidate' &&
  !field.proposedValues.some((value) =>
    sameSelectedValue(value, field.automaticDecision?.selected_value),
  )

// F59's owner controls (ADR-0085): enter a value, choose genres and withdraw a
// decision. Going back to an earlier value is choosing it again, from the same
// suggestions. Each is offered only where the store would accept it now; the
// store still decides, and a refusal is said here, beside the control used.
type OwnerOutcome = 'owner_entered' | 'withdraw'
// The width range where the Attention rail is a drawer over the workspace.
const drawerQuery = '(min-width: 761px) and (max-width: 1199px)'
// The open field's heading, the place focus returns to when the control that
// was used is gone.
const focusFieldHeading = () => {
  const heading = document.querySelector<HTMLElement>('.decision-canvas > header h3')
  heading?.setAttribute('tabindex', '-1')
  heading?.focus({ preventScroll: true })
}
const ownerText = (value: string) => value.normalize('NFC').replace(/\s+/gu, ' ').trim()
const quotedValue = (value: { value: unknown } | null | undefined) =>
  value ? `“${displayValue(value.value)}”` : 'nothing'
const withdrawCopy = {
  automatic_hold:
    'This decision is holding automatic handling, so it can’t be withdrawn. Use Resume automatic handling to release it.',
  in_progress:
    'This decision is being written to your library. You can change it once the write finishes.',
  written: writtenRefusal,
} as const

function OwnerControls({
  field,
  disabled,
  progress,
  status,
  failed,
  submit,
}: {
  field: ReviewField
  disabled: boolean
  progress: { command: DecisionCommand; phase: 'recording' | 'confirmed' | 'failed' } | null
  status: string
  failed: boolean
  submit: (outcome: OwnerOutcome, value: DecisionCommand['selectedValue']) => string
}) {
  const control = field.ownerControl ?? null
  const label = destinationLabel(field.destination)
  const [mode, setMode] = useState<'idle' | 'entry' | 'subset'>('idle')
  const [draft, setDraft] = useState('')
  const [typed, setTyped] = useState(false)
  const [chosen, setChosen] = useState<readonly string[]>([])
  const [problem, setProblem] = useState('')
  // The notice or refusal the owner has moved past by opening or closing a
  // form; it stays unsaid until the next command.
  const [dismissed, setDismissed] = useState<string | null>(null)
  const inputId = useId()
  const countId = useId()
  const problemId = useId()
  const input = useRef<HTMLInputElement>(null)
  const subsetGroup = useRef<HTMLFieldSetElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const recorded = useRef<HTMLParagraphElement>(null)
  const notice = useRef<HTMLParagraphElement>(null)
  const statusLine = useRef<HTMLParagraphElement>(null)
  const sent = useRef<{ key: string; outcome: OwnerOutcome } | null>(null)
  const busy = progress?.phase === 'recording'
  const decision = field.humanDecision
  // Any standing decision is changed, not entered afresh (D5).
  const changing = decision !== null && decision.outcome !== 'withdraw'
  const refocus = useRef<RefObject<HTMLButtonElement | null> | null>(null)
  useEffect(() => {
    if (mode === 'entry') input.current?.focus()
    if (mode === 'subset') subsetGroup.current?.querySelector('input')?.focus()
    if (mode === 'idle') refocus.current?.current?.focus({ preventScroll: true })
    refocus.current = null
  }, [mode])
  // A settled command leaves focus on something named: the recorded value,
  // the withdrawal's own outcome line where its controls were (#1365), or,
  // when a refusal removed the control that was used, the refusal itself.
  useEffect(() => {
    const command = sent.current
    if (!command) return
    const own = progress?.command.idempotencyKey === command.key ? progress : null
    if (own?.phase === 'recording') return
    sent.current = null
    if (own?.phase !== 'confirmed') {
      requestAnimationFrame(() => {
        if (!document.activeElement || document.activeElement === document.body)
          notice.current?.focus({ preventScroll: true })
      })
      return
    }
    setMode('idle')
    requestAnimationFrame(() => {
      ;(command.outcome === 'withdraw'
        ? statusLine.current
        : (recorded.current ?? notice.current)
      )?.focus({ preventScroll: true })
    })
  }, [progress])
  if (!control) return null
  const { entry, subset } = control
  const writing = control.withdraw === 'in_progress'
  const blocked = disabled || busy
  const send = (outcome: OwnerOutcome, value: DecisionCommand['selectedValue']) => {
    // The ref is the guard a second press meets before React re-renders.
    if (blocked || sent.current !== null) return
    setProblem('')
    setDismissed(null)
    sent.current = { key: submit(outcome, value), outcome }
  }
  const close = (target: RefObject<HTMLButtonElement | null>) => {
    refocus.current = target
    setMode('idle')
    setProblem('')
    setDismissed(status)
  }
  const escape = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return
    event.preventDefault()
    close(trigger)
  }
  const openEntry = () => {
    const recordedText = decision?.selected_value?.value
    setDraft(changing && typeof recordedText === 'string' ? recordedText : '')
    setTyped(false)
    setProblem('')
    setDismissed(status)
    setMode('entry')
  }
  const openSubset = () => {
    const recordedList = decision?.selected_value?.value
    setChosen(
      changing && Array.isArray(recordedList)
        ? (recordedList as string[])
        : (subset?.options.filter((option) => option.current).map((option) => option.value) ?? []),
    )
    setProblem('')
    setDismissed(status)
    setMode('subset')
  }
  const recordEntry = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!entry || blocked) return
    const value = ownerText(draft)
    setDraft(value)
    if (!value) setProblem('Enter a value first.')
    else if (Array.from(value).length > entry.maxCharacters)
      setProblem(`Use up to ${String(entry.maxCharacters)} characters.`)
    else if (value === field.currentRaw) setProblem('That is already the current value.')
    else send('owner_entered', { kind: 'text', value })
  }
  const recordSubset = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!subset || blocked) return
    // Raw values in the store's union order, never display text.
    const value = subset.options
      .map((option) => option.value)
      .filter((option) => chosen.includes(option))
    const current = Array.isArray(field.currentRaw) ? (field.currentRaw as unknown[]) : []
    if (!value.length) setProblem('Choose at least one genre.')
    else if (value.length > subset.maxItems)
      setProblem(`Choose up to ${String(subset.maxItems)} genres.`)
    else if (value.length === current.length && value.every((item, i) => item === current[i]))
      setProblem('Those are already the current genres.')
    else send('owner_entered', { kind: 'list', value })
  }
  const recordedCopy =
    decision?.outcome === 'owner_entered'
      ? Array.isArray(decision.selected_value?.value)
        ? `You chose ${String(decision.selected_value.value.length)} ${decision.selected_value.value.length === 1 ? 'genre' : 'genres'}.`
        : `You entered ${quotedValue(decision.selected_value)}.`
      : null
  // A refusal is said in one place: beside the input while a form is open,
  // and at the end of the section otherwise. Each place is mounted empty.
  const shown = status === dismissed ? '' : status
  const refusal = problem || (failed ? shown : '')
  const refusalNotice = (
    <p ref={notice} id={problemId} tabIndex={-1} className="owner-notice" role="alert">
      {refusal}
    </p>
  )
  return (
    <section className="owner-controls" aria-label={`Your value for ${label}`}>
      {recordedCopy ? (
        <p ref={recorded} tabIndex={-1} className="owner-recorded">
          {recordedCopy}
        </p>
      ) : null}
      {writing ? <p className="owner-note">{withdrawCopy.in_progress}</p> : null}
      {entry && mode !== 'entry' && !writing ? (
        <button ref={trigger} type="button" className="owner-trigger" onClick={openEntry}>
          {changing ? 'Change' : 'Enter a value'}
        </button>
      ) : null}
      {subset && mode !== 'subset' && !writing ? (
        <button ref={trigger} type="button" className="owner-trigger" onClick={openSubset}>
          {changing ? 'Change' : 'Choose genres'}
        </button>
      ) : null}
      {entry && mode === 'entry' ? (
        <form className="owner-entry" onSubmit={recordEntry} onKeyDown={escape} noValidate>
          <label htmlFor={inputId}>{`Your value for ${label}`}</label>
          <input
            ref={input}
            id={inputId}
            type="text"
            value={draft}
            autoComplete="off"
            aria-invalid={refusal ? true : undefined}
            aria-describedby={`${countId} ${problemId}`}
            onChange={(event) => {
              setDraft(event.target.value)
              setTyped(true)
            }}
            onBlur={() => setDraft((value) => ownerText(value))}
          />
          <small id={countId} className="owner-count" aria-live="polite">
            {typed
              ? `${String(Array.from(draft).length)} of ${String(entry.maxCharacters)} characters`
              : ''}
          </small>
          {refusalNotice}
          {entry.suggestions.length ? (
            <div className="owner-suggestions">
              {entry.suggestions.map((suggestion) => (
                <button
                  key={suggestion.rule}
                  type="button"
                  onClick={() => {
                    setDraft(suggestion.value)
                    setTyped(true)
                    input.current?.focus()
                  }}
                >
                  {suggestion.rule === 'previous' ? 'Use previous value ' : 'Use '}
                  {/* Library text may break anywhere; the copy around it never does. */}
                  <span className="owner-suggestion-value">“{suggestion.value}”</span>
                </button>
              ))}
            </div>
          ) : null}
          <div className="owner-actions">
            <button type="submit" aria-disabled={blocked}>
              {busy ? 'Recording…' : 'Record this value'}
            </button>
            <button type="button" onClick={() => close(trigger)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {subset && mode === 'subset' ? (
        <form className="owner-entry" onSubmit={recordSubset} onKeyDown={escape} noValidate>
          <fieldset ref={subsetGroup} className="owner-subset" aria-describedby={problemId}>
            <legend>Choose genres</legend>
            {subset.options.map((option) => (
              <label key={option.value} className="owner-option">
                <input
                  type="checkbox"
                  checked={chosen.includes(option.value)}
                  onChange={(event) => {
                    const checked = event.target.checked
                    setChosen((values) =>
                      checked
                        ? [...values, option.value]
                        : values.filter((value) => value !== option.value),
                    )
                  }}
                />
                <span>{option.value}</span>
                {/* One unit, so a narrow row wraps its tags together. */}
                <span className="owner-tags">
                  {option.current ? <StatusPill>Current</StatusPill> : null}
                  {option.proposed ? <StatusPill>Proposed</StatusPill> : null}
                  {option.previous ? <StatusPill>Previous</StatusPill> : null}
                </span>
              </label>
            ))}
          </fieldset>
          {refusalNotice}
          <div className="owner-actions">
            <button type="submit" aria-disabled={blocked}>
              {busy ? 'Recording…' : 'Record these genres'}
            </button>
            <button type="button" onClick={() => close(trigger)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {control.withdraw === 'available' ? (
        <button
          type="button"
          className="owner-trigger"
          aria-disabled={blocked}
          onClick={() => send('withdraw', null)}
        >
          {busy && progress.command.outcome === 'withdraw' ? 'Withdrawing…' : 'Withdraw'}
        </button>
      ) : control.withdraw &&
        control.withdraw !== 'in_progress' &&
        // The refusal already says it in the same words.
        !(control.withdraw === 'written' && refusal === writtenRefusal) ? (
        <p className="owner-note">{withdrawCopy[control.withdraw]}</p>
      ) : null}
      {mode === 'idle' ? refusalNotice : null}
      <p ref={statusLine} tabIndex={-1} className="owner-status" role="status">
        {refusal || failed ? '' : shown}
      </p>
    </section>
  )
}

function RecordedOutcome({
  field,
  mediaKind,
  expanded,
  toggle,
}: {
  field: ReviewField
  mediaKind: string
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
  const context = selectedAutomaticLanguageForm(field)
    ? field.choiceContexts.find((item) => item.recommendation === 'recommended')
    : selectedProposed
      ? choiceContext(
          field,
          field.choiceContexts.filter((item) => item.selection === 'proposed')[proposed]
            ?.alternativeId ?? null,
        )
      : choiceContext(field, null)
  const combinedTags = combinedAutomaticTags(field, mediaKind)
  const evidenceId = `recorded-evidence-${field.destination}`
  const accepted = decision.outcome === 'accept' || decision.outcome === 'candidate'
  return (
    <section
      className="recorded-outcome"
      aria-label={`${destinationLabel(field.destination)} recorded outcome`}
    >
      <p>
        <strong>
          {field.humanDecision === null && field.automaticDecision !== null && !field.manualHold
            ? 'Resolved automatically'
            : completionOutcome(field)}
        </strong>
      </p>
      <dl>
        {accepted ? (
          <div>
            <dt>Chosen value</dt>
            <dd>
              {field.destination === 'description' ? (
                <DescriptionText key={decisionValue(field)} value={decisionValue(field)} />
              ) : (
                decisionValue(field)
              )}
            </dd>
          </div>
        ) : (
          <div>
            <dt>
              {decision.outcome === 'keep_current'
                ? 'Retained value'
                : decision.outcome === 'owner_entered'
                  ? 'Your value'
                  : 'Current value'}
            </dt>
            <dd>
              {field.destination === 'description' ? (
                <DescriptionText key={decisionValue(field)} value={decisionValue(field)} />
              ) : (
                decisionValue(field)
              )}
            </dd>
          </div>
        )}
      </dl>
      {selectedAutomaticLanguageForm(field) ? (
        <p>The policy chose this observed form of the equivalent language evidence.</p>
      ) : null}
      {combinedTags ? (
        <p>Compatible source tags were combined, retaining any existing tags.</p>
      ) : null}
      <ProviderConsensusAutomaticStatement field={field} />
      <DescriptionAutomaticStatement field={field} />
      <SeriesAutomaticStatement field={field} />
      <NarratorsAutomaticStatement field={field} />
      <AsinAutomaticStatement field={field} />
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
        combinedTags ? (
          <div id={evidenceId}>
            {field.choiceContexts
              .filter((item) => item.evidence.length > 0)
              .map((item, index) => (
                <ChoiceEvidencePanel
                  key={item.alternativeId ?? 'current'}
                  context={item}
                  evidenceId={`${evidenceId}-${String(index)}`}
                  label={
                    item.selection === 'current'
                      ? 'Retained tags'
                      : `Source tags ${String(index + 1)}`
                  }
                />
              ))}
          </div>
        ) : (
          <ChoiceEvidencePanel
            context={context}
            evidenceId={evidenceId}
            label={`${destinationLabel(field.destination)} recorded outcome`}
          />
        )
      ) : null}
    </section>
  )
}

export default function LibraryPage({
  data,
  retained,
  pending,
  failed,
  retry,
  onRailRetaining,
}: {
  data: LibraryView | undefined
  /** `data` is the previous key's result, shown busy until the URL's own read settles. */
  retained: boolean
  pending: boolean
  failed: boolean
  retry: () => void
  /** Reports whether the attention rail is showing a retained queue. */
  onRailRetaining?: (retaining: boolean) => void
}) {
  const works = useMemo(() => data?.works ?? [], [data?.works])
  // Retained rows stay visible, but selection resolves only against the URL's own result.
  const currentWorks = retained ? noWorks : works
  const reading = pending || retained
  const search = libraryRoute.useSearch()
  const libraryNavigate = useNavigate({ from: '/library' })
  const router = useRouter()
  // Library stays painted while the next route's code loads, but it no longer
  // owns the URL, so a late selection or advance cannot pull the owner back.
  const navigate: typeof libraryNavigate = useCallback(
    (options) =>
      router.latestLocation.pathname === '/library' ? libraryNavigate(options) : Promise.resolve(),
    [libraryNavigate, router],
  )
  const { http, updates, queryClient } = useReviewContext()
  // Global figures read the library summary, which carries no q (#1284).
  const summary = useQuery(librarySummaryQuery(http))
  const [lensCount, lensWords] = lensMatching[search.lens]
  const lensTotal = summary.data?.counts[lensCount]
  const summaryAttention = summary.data?.counts.attention
  const summaryNotMatched = summary.data?.counts.notMatched
  const summaryFailed = summary.data?.counts.failed
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
        now: presentation.now,
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
  const pendingSearchQuery = useRef<{ q: string | null; attention?: boolean } | null>(null)
  const searchNavigation = useRef<Promise<void> | null>(null)
  const searchCommitTimer = useRef<number | null>(null)
  const searchGeneration = useRef(0)
  const observedSearchIdentity = useRef(librarySearchIdentity(search))
  const expectedSearchIdentity = useRef<string | null>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const lensRow = useRef<HTMLElement>(null)
  // The lenses scroll sideways in one row. An edge with more lenses beyond it
  // fades, so a row that happens to end between two lenses still shows it
  // continues. The current lens is brought into the row's view, clear of the
  // fade, without moving the page: on load, on a lens change, and when the row
  // or its lenses change size, as with enlarged text.
  useLayoutEffect(() => {
    const row = lensRow.current
    if (!row) return
    const mark = () => {
      const start = row.scrollLeft > 1
      const end = row.scrollLeft + row.clientWidth < row.scrollWidth - 1
      if (start || end) row.dataset.more = start && end ? 'both' : start ? 'start' : 'end'
      else delete row.dataset.more
    }
    // The first and last lenses scroll the row to its end, so no fade covers
    // them; any other lens is brought clear of both fades.
    const reveal = (lens: Element | null) => {
      if (lens instanceof HTMLElement) {
        let left = row.scrollLeft
        if (lens === row.firstElementChild) left = 0
        else if (lens === row.lastElementChild) left = row.scrollWidth - row.clientWidth
        else {
          const fade = parseFloat(getComputedStyle(row).scrollPaddingInlineStart) || 0
          const rowBox = row.getBoundingClientRect()
          const box = lens.getBoundingClientRect()
          // A lens too wide to clear both fades is centred, or starts at the
          // row's edge when even the row is narrower, so its label's start shows.
          if (box.width > rowBox.width - 2 * fade)
            left +=
              box.width > rowBox.width
                ? box.left - rowBox.left
                : box.left - rowBox.left - (rowBox.width - box.width) / 2
          else if (box.left < rowBox.left + fade) left -= rowBox.left + fade - box.left
          else if (box.right > rowBox.right - fade) left += box.right - (rowBox.right - fade)
        }
        if (Math.abs(left - row.scrollLeft) > 0.5) row.scrollLeft = left
      }
      mark()
    }
    const revealCurrent = () => reveal(row.querySelector('[aria-current]'))
    // Focus leaves a partly shown lens where it is, so Tab reveals it too.
    const revealFocused = (event: FocusEvent) => reveal(event.target as Element | null)
    revealCurrent()
    row.addEventListener('scroll', mark, { passive: true })
    row.addEventListener('focusin', revealFocused)
    const resized = typeof ResizeObserver === 'function' ? new ResizeObserver(revealCurrent) : null
    for (const node of [row, ...row.children]) resized?.observe(node)
    return () => {
      row.removeEventListener('scroll', mark)
      row.removeEventListener('focusin', revealFocused)
      resized?.disconnect()
    }
  }, [search.lens])
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
  const attentionHeading = useRef<HTMLHeadingElement>(null)
  const attentionToggle = useRef<HTMLButtonElement>(null)
  const attentionRetry = useRef<HTMLButtonElement>(null)
  // Close and Escape hand focus back to the toggle that opened the rail.
  const returnToToggle = useRef(false)
  const returnToWork = useRef(false)
  const revealWorkspace = useRef(false)
  const revealQueueChoice = useRef(false)
  const workSelectionGeneration = useRef(0)
  const selectedIdentityRef = useRef<{
    id: string
    title: string
    authors: readonly string[]
    library: string
    libraryDisplayName?: string
    mediaKind: MediaKind
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
  // The preview whose detail was last shown ready, until the preview changes.
  const selectedDetailShown = useRef<string | null>(null)
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
    identity: {
      title: string
      authors: readonly string[]
      library: string
      libraryDisplayName?: string
      mediaKind: MediaKind
    }
  } | null>(null)
  const [completionInspection, setCompletionInspection] = useState<string | null>(null)
  const [automaticPanel, setAutomaticPanel] = useState<string | null>(null)
  const [ownerCommand, setOwnerCommand] = useState<string | null>(null)
  // What the owner is told on the case they were moved to when theirs was
  // superseded under them, and the case whose field heading takes focus (M4).
  const [ownerNotice, setOwnerNotice] = useState<{
    caseId: string
    destination: string
    text: string
  } | null>(null)
  const headingFocus = useRef<string | null>(null)
  // The case being left for its successor: while it is, its own failed read
  // and its absence from Library are the move, not something to report.
  const [movingFrom, setMovingFrom] = useState<string | null>(null)
  // The selected case once any path knows a successor is pending for it (a
  // lineage check, a Library update naming one, or a stale command): until
  // the move settles, its absence from Library is that move, not news. It is
  // set where the successor becomes known and cleared only where a move ends,
  // since a move can start before a lineage check's own update lands.
  const [successorPending, setSuccessorPending] = useState<string | null>(null)
  const ownerField = useRef<string | null>(null)
  // The case whose owner form was open, or whose owner control had focus,
  // when the Library said it changed; the refetch that follows can unmount it.
  const ownerEngaged = useRef<string | null>(null)
  const [automaticResults, setAutomaticResults] = useState<
    Readonly<Record<string, 'success' | 'error' | 'refreshed'>>
  >({})
  const staleCoverRecoveryButton = useRef<HTMLButtonElement>(null)
  const [staleCoverReview, setStaleCoverReview] = useState<{
    caseId: string
    title: string
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
      setSuccessorPending(null)
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
    ? (currentWorks.find((work) => work.workId === search.work) ??
      (openedWork?.workId === search.work ? openedWork : undefined))
    : undefined
  const unresolvableWork =
    data !== undefined && !retained && Boolean(search.work) && selectedWork === undefined
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
    ? currentWorks.find((work) => work.reviewCaseId === search.preview)
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
    queryFn: (queryContext) =>
      http.getCaseSuccessor(
        lineageReconciliation?.predecessor ?? '',
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    enabled: activeLineageReconciliation,
    retry: (failureCount) => activeLineageReconciliation && failureCount < 1,
    retryDelay: coalesceMilliseconds,
    retryOnMount: false,
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
  })
  // Keep the queue this rail last showed while a newer search reads; a failed
  // read shows nothing, so it leaves nothing to revive.
  const showingAttention = useRef(false)
  const attentionQuery = useInfiniteQuery({
    queryKey: ['attention', 'workspace', search.q ?? null, search.attentionCursor ?? null],
    initialPageParam: search.attentionCursor,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: (queryContext) =>
      http.getAttention(
        {
          ...(search.q ? { q: search.q } : {}),
          ...(queryContext.pageParam ? { cursor: queryContext.pageParam } : {}),
        },
        queryContext.signal,
        queryWorkClass(queryContext),
      ),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    staleTime: 15_000,
    retry: false,
    placeholderData: (previous) => (showingAttention.current ? previous : undefined),
  })
  useEffect(() => {
    showingAttention.current = attentionQuery.data !== undefined
  })
  useLayoutEffect(
    () => onRailRetaining?.(attentionQuery.isPlaceholderData),
    [onRailRetaining, attentionQuery.isPlaceholderData],
  )
  const attention = useMemo(
    () => ({
      ...attentionQuery,
      refetch: () =>
        runRequiredRead(
          queryClient,
          ['attention', 'workspace', search.q ?? null, search.attentionCursor ?? null],
          () => attentionQuery.refetch(),
        ),
      fetchNextPage: () =>
        runRequiredRead(
          queryClient,
          ['attention', 'workspace', search.q ?? null, search.attentionCursor ?? null],
          () => attentionQuery.fetchNextPage(),
        ),
    }),
    [attentionQuery, queryClient, search.q, search.attentionCursor],
  )
  useRequiredRead(queryClient, [
    'attention',
    'workspace',
    search.q ?? null,
    search.attentionCursor ?? null,
  ])
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
  // One group per work, in first-appearance order; a work that continues onto
  // the next page joins its existing group.
  const attentionGroups = useMemo(() => {
    const groups = new Map<string, (typeof attentionItems)[number][]>()
    for (const item of attentionItems) {
      const group = groups.get(item.id)
      if (group) group.push(item)
      else groups.set(item.id, [item])
    }
    return [...groups.values()]
  }, [attentionItems])
  // Load more keeps its place: once the page lands, focus moves to the first row
  // it added, even when that row joins a work already in the rail (#1284). The
  // press ends at the query's next data or error update, whether or not a render
  // saw it fetching, so a refetch that cancels the page ends it too; a new search
  // drops it, and focus moves only for an added page the owner has not left.
  const attentionKey = `${search.q ?? ''} ${search.attentionCursor ?? ''}`
  const loadMore = useRef<{
    key: string
    pages: number
    updated: number
    errors: number
    from: number
  } | null>(null)
  useEffect(() => {
    const pending = loadMore.current
    if (!pending) return
    if (pending.key !== attentionKey || attentionQuery.errorUpdateCount > pending.errors) {
      loadMore.current = null
      return
    }
    const pages = attention.data?.pages.length ?? 0
    if (attentionQuery.dataUpdatedAt === pending.updated && pages <= pending.pages) return
    loadMore.current = null
    if (pages <= pending.pages) return
    const active = document.activeElement
    if (active && active !== document.body && !attentionRail.current?.contains(active)) return
    const first = attentionItems[pending.from]
    const row = first
      ? [
          ...(attentionRail.current?.querySelectorAll<HTMLElement>('[data-attention-row]') ?? []),
        ].find((node) => node.dataset.attentionRow === `${first.id}:${first.destination}`)
      : undefined
    ;(row ?? attentionRail.current)?.focus()
  }, [
    attention.data,
    attentionItems,
    attentionKey,
    attentionQuery.dataUpdatedAt,
    attentionQuery.errorUpdateCount,
  ])
  const selectedIdentity = useMemo(
    () =>
      search.preview
        ? (currentWorks.find((work) => work.reviewCaseId === search.preview) ??
          (attention.isPlaceholderData
            ? undefined
            : attentionItems.find((item) => item.id === search.preview)) ??
          // A readable case names its own work, so the selection survives a
          // Library result or Attention page that no longer lists it.
          (!detail.isError && detail.data?.id === search.preview
            ? {
                id: detail.data.id,
                title: detail.data.title,
                authors: detail.data.authors,
                library: detail.data.target.library,
                libraryDisplayName:
                  detail.data.target.libraryDisplayName ?? detail.data.target.library,
                mediaKind: detail.data.target.mediaKind,
              }
            : undefined))
        : undefined,
    [
      attentionItems,
      attention.isPlaceholderData,
      currentWorks,
      detail.data,
      detail.isError,
      search.preview,
    ],
  )
  useEffect(() => {
    if (selectedIdentity && search.preview)
      selectedIdentityRef.current = {
        id: search.preview,
        title: selectedIdentity.title,
        authors: selectedIdentity.authors,
        library: selectedIdentity.library,
        libraryDisplayName: selectedIdentity.libraryDisplayName ?? selectedIdentity.library,
        mediaKind: selectedIdentity.mediaKind,
      }
  }, [search.preview, selectedIdentity])
  useEffect(() => {
    if (search.preview && detail.data?.id === search.preview) {
      selectedTargetRef.current = { caseId: search.preview, ...detail.data.target }
      if (pendingSuccessorRef.current.size) {
        setSuccessorPending(search.preview)
        setSuccessorGeneration((generation) => generation + 1)
      }
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
    const named = lineageSuccessor.data
    const following =
      named !== null && pendingSuccessorRef.current.size < maxCoalescedSuccessorCandidates
    queueMicrotask(() => {
      setActiveLineageGeneration(null)
      setLineageReconciliation(null)
      if (following && selectedTargetRef.current?.caseId === reconciliation.selection)
        setSuccessorPending(reconciliation.selection)
    })
    if (following) {
      pendingSuccessorRef.current.add(named)
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
      if (
        document.querySelector(
          '.owner-controls form, .owner-controls button:focus, .owner-controls input:focus',
        )
      )
        ownerEngaged.current = search.preview ?? null
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
        if (selectedTargetRef.current?.caseId === search.preview)
          setSuccessorPending(search.preview ?? null)
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
      !data ||
      retained ||
      !selectedTarget ||
      !successorIds.length ||
      selectedTarget.caseId !== search.preview ||
      works.some((work) => work.reviewCaseId === search.preview)
    )
      return
    pendingSuccessorRef.current.clear()
    setMovingFrom(selectedTarget.caseId)
    void (async () => {
      try {
        const predecessor = await http.getCaseDetail(selectedTarget.caseId)
        if (
          predecessor.current ||
          predecessor.target.connection !== selectedTarget.connection ||
          predecessor.target.library !== selectedTarget.library ||
          predecessor.target.item !== selectedTarget.item
        ) {
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        }
      } catch (error) {
        // The server refuses a superseded case's read as stale; that is the
        // case no longer being current. Each successor's target is checked.
        if (!(error instanceof ApiError && error.code === 'stale')) {
          // The check itself failed, so the case is read again while what the
          // owner has open stays; a failed read then drops it (#1365).
          void queryClient.refetchQueries({
            queryKey: detailQuery(http, selectedTarget.caseId).queryKey,
            exact: true,
          })
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        }
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
          const destination = ownerField.current
          if (destination && ownerEngaged.current === selectedTarget.caseId) {
            ownerEngaged.current = null
            setOwnerNotice({
              caseId: successor.id,
              destination,
              text: 'The work changed. Review it again.',
            })
            headingFocus.current = successor.id
          }
          await navigate({
            replace: true,
            search: (current) =>
              current.preview === selectedTarget.caseId
                ? { ...current, preview: successor.id }
                : current,
          })
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        } catch {
          // A case can disappear again between the bounded page read and its
          // authoritative detail read. The remaining coalesced candidates may
          // still name its replacement.
        }
      }
      // A move that ends nowhere leaves a predecessor its own read refused, so
      // its retained case read is read again rather than shown as current.
      void queryClient.resetQueries({
        queryKey: detailQuery(http, selectedTarget.caseId).queryKey,
        exact: true,
      })
      setMovingFrom(null)
      setSuccessorPending(null)
    })()
  }, [data, retained, works, http, navigate, queryClient, search.preview, successorGeneration])
  const completionIdentity = complete?.id === selectedReviewCaseId ? complete.identity : undefined
  const [pendingIdentity, setPendingIdentity] = useState<{
    id: string
    identity: NonNullable<typeof selectedIdentity>
  } | null>(null)
  if (selectedIdentity && search.preview) {
    if (pendingIdentity?.id !== search.preview || pendingIdentity.identity !== selectedIdentity)
      setPendingIdentity({ id: search.preview, identity: selectedIdentity })
  } else if (pendingIdentity && (!reading || failed || pendingIdentity.id !== search.preview))
    setPendingIdentity(null)
  const displayedIdentity =
    selectedIdentity ??
    completionIdentity ??
    (reading && !failed && pendingIdentity !== null && pendingIdentity.id === search.preview
      ? pendingIdentity.identity
      : undefined)
  const visibleFields = detail.data?.fields.filter(primaryField) ?? []
  const currentActionableDestinations = visibleFields.map((field) => field.destination)
  const frozenDestinations =
    frozenFieldOrders[selectedReviewCaseId] ?? currentActionableDestinations
  const railFields = frozenDestinations
    .map((destination) => visibleFields.find((field) => field.destination === destination))
    .filter((field): field is ReviewField => field !== undefined)
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
  const selectedOwnerDestination = selected?.ownerControl ? selected.destination : null
  useEffect(() => {
    ownerField.current = selectedOwnerDestination
  }, [selectedOwnerDestination])
  const shownCaseId = detail.data?.id
  useEffect(() => {
    if (!shownCaseId || headingFocus.current !== shownCaseId) return
    headingFocus.current = null
    requestAnimationFrame(focusFieldHeading)
  }, [shownCaseId])
  // An owner write settles after its decision recorded. The case topic is not
  // published then, so while one is in flight each Activity frame re-reads the
  // open case, and its controls stop saying the write is in progress (O1).
  const ownerWriteInFlight =
    detail.data?.fields.some((field) => field.ownerControl?.withdraw === 'in_progress') === true
  useEffect(() => {
    if (!updates || !ownerWriteInFlight || !shownCaseId) return
    return updates.subscribe((update) => {
      if (update.topic !== 'activity') return
      void invalidateBackgroundQueries(queryClient, {
        queryKey: ['review-case', shownCaseId],
        exact: true,
      })
    })
  }, [ownerWriteInFlight, queryClient, shownCaseId, updates])
  const contextualFields =
    detail.data?.fields.filter((field) => field.inventoryRole === 'contextual') ?? []
  const contextualSummary = {
    unchanged: contextualFields.filter((field) => contextKind(field) === 'unchanged'),
    waiting: contextualFields.filter((field) => contextKind(field) === 'waiting').length,
    unavailable: contextualFields.filter((field) => contextKind(field) === 'unavailable').length,
  }
  const activeQueueIndex = selected
    ? attentionItems.findIndex(
        (item) => item.id === search.preview && item.destination === selected.destination,
      )
    : -1
  const activeQueueGroup = attentionGroups.findIndex((group) => group[0]?.id === search.preview)
  // The summary and this search's read are separate reads; clamp any skew.
  const outsideSearch = Math.max((summaryAttention ?? 0) - (data?.counts.attention ?? 0), 0)
  const nextWork =
    complete && !attention.isPlaceholderData
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
            // A retained rail is the previous search's queue, not a prefix of the next one.
            (activeQueueIndex < 0 || attention.isPlaceholderData
              ? []
              : attentionItems.slice(0, activeQueueIndex + 1)
            ).map((item) => `${item.id}:${item.destination}`),
          ),
          identity: retained,
        })
      }
    },
    onRecorded: (caseId, destination) => {
      if (destination !== 'cover') return
      setStaleCoverReview((current) => (current?.caseId === caseId ? null : current))
    },
    onStale: async (command) => {
      if (ownerOutcome(command.outcome)) {
        // The owner stays on the work: the refused command is said on the case
        // that replaced theirs, with focus on its field (M4).
        const predecessor = selectedReviewCaseId
        setMovingFrom(predecessor)
        setSuccessorPending(predecessor)
        const successor = await http.getCaseSuccessor(predecessor).catch(() => null)
        if (successor !== null) {
          setReconciledSuccessorIdentity(successor)
          setOwnerNotice({
            caseId: successor,
            destination: command.destination,
            text: 'Not recorded: the work changed. Review it again.',
          })
          headingFocus.current = successor
          await navigate({
            replace: true,
            search: (current) =>
              current.preview === predecessor
                ? { ...current, preview: successor, field: command.destination }
                : current,
          })
          setMovingFrom(null)
          setSuccessorPending(null)
          return
        }
        setMovingFrom(null)
        setSuccessorPending(null)
      }
      if (selected?.destination === 'cover') {
        setStaleCoverReview({
          caseId: selectedReviewCaseId,
          title: selectedIdentityRef.current?.title ?? 'Cover comparison',
          failed: false,
          loading: false,
        })
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
  const staleCoverCaseId = staleCoverReview?.caseId
  const staleCoverIdentityAvailable = Boolean(displayedIdentity)
  useEffect(() => {
    if (!staleCoverCaseId) return
    const frame = requestAnimationFrame(() => {
      if (document.activeElement === document.body) staleCoverRecoveryButton.current?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [staleCoverCaseId, staleCoverIdentityAvailable])
  const staleCoverNotice =
    staleCoverReview?.caseId === selectedReviewCaseId ? (
      <aside className="comparison-notice" role="alert">
        {!displayedIdentity || detail.isError ? <h2>{staleCoverReview.title}</h2> : null}
        <p>This cover comparison changed before your choice was recorded. No cover was changed.</p>
        {staleCoverReview.failed ? (
          <p>The current comparison could not be loaded. Your prior choice remains unrecorded.</p>
        ) : null}
        <button
          type="button"
          ref={staleCoverRecoveryButton}
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
    onError: (error, request) =>
      setAutomaticResults((current) => ({
        ...current,
        [request.key]: error instanceof CsrfRenewedError ? 'refreshed' : 'error',
      })),
  })

  const routeIdentity = `${search.lens}:${search.q ?? ''}:${search.cursor ?? ''}:${search.attentionCursor ?? ''}:${search.preview ?? ''}:${search.work ?? ''}:${search.attention ?? ''}:${search.preparation ?? ''}`
  useRouteReadyFocus(routeIdentity, true)
  useEffect(() => {
    if (!preparationSelected) return
    const frame = requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>('.preparation-detail [data-route-focus]')
        ?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [preparationSelected, presentation?.run_id])
  const identityPending =
    !displayedIdentity &&
    (reading || attention.isPending || attention.isPlaceholderData || detail.isPending)
  const identityError = !displayedIdentity && (failed || attention.isError)
  // While a command records, the Library asks for the successor, the owner is
  // moved to it, or its identity is still being read, the absence is the move.
  const leaving =
    movingFrom === search.preview ||
    activeLineageReconciliation ||
    controller.recording ||
    (reconciledSuccessorIdentity === search.preview && detail.isPending)
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
    selectedDetailShown.current = null
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
    if (selectedDetailReady) selectedDetailShown.current = search.preview ?? null
  }, [selectedReadError, search.preview, selectedDetailReady])
  // A work chosen from the attention queue takes focus on its header without
  // scrolling. With enlarged text the header can sit below the fold, or under
  // the masthead, so once it holds focus it is brought fully into view; the
  // page's scroll padding keeps it clear of the masthead (#1487). Where Back to
  // works leads the work, the reveal starts there instead, so it stays in view
  // with the header beneath it, as much of it as the screen holds (#1530).
  useEffect(() => {
    if (!revealQueueChoice.current || !(selectedDetailReady || selectedReadError)) return
    revealQueueChoice.current = false
    let revealFrame = 0
    const focusFrame = requestAnimationFrame(() => {
      revealFrame = requestAnimationFrame(() => {
        const active = document.activeElement
        if (!(active instanceof HTMLElement && active.closest('#main-content .workspace-detail')))
          return
        const back = document.querySelector<HTMLElement>('.compact-workspace-back')
        if (!back?.getClientRects().length) {
          active.scrollIntoView({ block: 'nearest' })
          return
        }
        const masthead = document.querySelector<HTMLElement>('.masthead')?.getBoundingClientRect()
        const clear = Math.max(0, masthead?.bottom ?? 0)
        const top = back.getBoundingClientRect().top
        if (top < clear || active.getBoundingClientRect().bottom > window.innerHeight)
          window.scrollBy({ top: top - clear })
      })
    })
    return () => {
      cancelAnimationFrame(focusFrame)
      cancelAnimationFrame(revealFrame)
    }
  }, [search.preview, search.field, selectedDetailReady, selectedReadError])
  // A detail that was shown and then could not be read again, as after a
  // command whose follow-up reads fail, takes away the control that had focus;
  // its Try again takes focus rather than the page (#1288).
  useEffect(() => {
    if (!search.preview || !selectedReadError || leaving) return
    if (document.activeElement !== document.body) return
    if (selectedDetailShown.current !== search.preview) return
    selectedDetailRetryButton.current?.focus({ preventScroll: true })
  }, [leaving, search.preview, selectedReadError])
  useEffect(() => {
    currentSearch.current = search
  }, [search])
  useEffect(() => {
    if (controller.canRetry) retryButton.current?.focus()
  }, [controller.canRetry])
  useEffect(() => {
    if (complete?.id !== selectedReviewCaseId || completionInspection !== null) return
    completionRegion.current?.focus({ preventScroll: true })
    // The card replaces the last choice where it stood, so its heading can
    // start under the masthead; the page's scroll padding keeps it clear (#1490).
    completionRegion.current?.querySelector('h3')?.scrollIntoView({ block: 'nearest' })
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
    if (!selectedDetailReady || !rail || !active) return
    centreFieldRail(rail, active)
    if (!advanceFocus.current) return
    advanceFocus.current = false
    // The next field's choices take focus at their one Tab stop (#1290).
    const action =
      document.querySelector<HTMLButtonElement>('.decision-canvas [role="radio"][tabindex="0"]') ??
      document.querySelector<HTMLButtonElement>('.decision-canvas button:not(:disabled)')
    action?.focus({ preventScroll: true })
    if (!action) return
    const bounds = action.getBoundingClientRect()
    const masthead = document.querySelector<HTMLElement>('.masthead')
    const visibleTop = Math.max(0, masthead?.getBoundingClientRect().bottom ?? 0) + 8
    const reveal =
      bounds.top < visibleTop
        ? bounds.top - visibleTop
        : Math.max(0, bounds.bottom - window.innerHeight)
    if (reveal) window.scrollBy({ top: reveal })
  }, [centreFieldRail, selected?.destination, selectedDetailReady])
  // At compact widths the list gives way to the work, so an owner who chose one
  // far down the page is brought to the workspace's top, where Back to works
  // leads it, just below the sticky masthead (#1284). These selections skip the
  // router's reset to the top, and the work may still be loading, so the reveal
  // repeats until it lands or the work settles. Closing the rail over a selected
  // work reveals it the same way.
  useLayoutEffect(() => {
    if (!revealWorkspace.current || !(search.preview ?? search.work)) return
    const workspace = document.querySelector<HTMLElement>('.library-workspace')
    if (!workspace || !compactWidth()) {
      revealWorkspace.current = false
      return
    }
    const masthead = document.querySelector<HTMLElement>('.masthead')?.getBoundingClientRect()
    const top =
      window.scrollY + workspace.getBoundingClientRect().top - Math.max(0, masthead?.bottom ?? 0)
    window.scrollTo({ top })
    if (Math.abs(window.scrollY - top) <= 1 || selectedDetailReady || selectedReadError)
      revealWorkspace.current = false
  }, [search.attention, search.preview, search.work, selectedDetailReady, selectedReadError])
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
    const opened = search.attention === 'open'
    if (!opened && !returnToToggle.current) return
    returnToToggle.current = false
    let focusFrame = 0
    const routeFrame = requestAnimationFrame(() => {
      focusFrame = requestAnimationFrame(() => {
        // Closing returns focus from the rail it left, or from where the
        // route's own focus put it, never from a field the owner moved to.
        const active = document.activeElement
        const routeTarget = document.querySelector('#main-content [data-route-focus]')
        // At 760px and below closing over a selected work reveals that work,
        // so focus stays on its header there rather than the toggle above it.
        if (opened) {
          attentionHeading.current?.focus({ preventScroll: true })
          attentionHeading.current?.scrollIntoView({ block: 'nearest' })
        } else if (
          !active ||
          active === document.body ||
          active === routeTarget ||
          attentionRail.current?.contains(active)
        ) {
          ;(returnToWork.current && routeTarget instanceof HTMLElement
            ? routeTarget
            : attentionToggle.current
          )?.focus({ preventScroll: true })
        }
      })
    })
    return () => {
      cancelAnimationFrame(routeFrame)
      cancelAnimationFrame(focusFrame)
    }
  }, [search.attention])
  // From 761 to 1199px the rail is a drawer over the workspace: everything
  // outside it but the toolbar that toggles it is inert until it closes, and
  // its own Tab order wraps.
  useEffect(() => {
    const rail = attentionRail.current
    if (search.attention !== 'open' || !rail || typeof window.matchMedia !== 'function') return
    const drawer = window.matchMedia(drawerQuery)
    let muted: Element[] = []
    const restore = () => {
      for (const node of muted) node.removeAttribute('inert')
      muted = []
    }
    const apply = () => {
      restore()
      if (!drawer.matches) return
      for (let node: Element = rail; node.parentElement && node !== document.body;) {
        const parent: Element = node.parentElement
        for (const sibling of parent.children)
          if (
            sibling !== node &&
            !sibling.hasAttribute('inert') &&
            !(attentionToggle.current && sibling.contains(attentionToggle.current))
          ) {
            sibling.setAttribute('inert', '')
            muted.push(sibling)
          }
        node = parent
      }
    }
    apply()
    drawer.addEventListener('change', apply)
    return () => {
      drawer.removeEventListener('change', apply)
      restore()
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
      if (
        !media.matches ||
        searchPending ||
        currentSearch.current.preview ||
        currentSearch.current.work ||
        currentSearch.current.preparation
      )
        return
      const first =
        currentWorks.find((work) => work.reviewCaseId && work.attentionCount > 0) ??
        currentWorks.find((work) => work.reviewCaseId)
      const reviewCaseId = first?.reviewCaseId
      if (reviewCaseId)
        void navigate({
          replace: true,
          search: (current) =>
            current.preview || current.work || current.preparation
              ? current
              : { ...current, preview: reviewCaseId },
        })
    }
    selectWideDefault()
    media.addEventListener('change', selectWideDefault)
    return () => media.removeEventListener('change', selectWideDefault)
  }, [currentWorks, navigate, searchPending])

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
      resetScroll: !revealWorkspace.current && !(field && reviewCaseId === selectedReviewCaseId),
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
    // A retained row may not be on the page the URL now names.
    if (retained) return
    revealWorkspace.current = compactWidth()
    if (work.reviewCaseId) {
      selectCase(work.reviewCaseId)
      return
    }
    setComplete(null)
    setCompletionInspection(null)
    const generation = ++workSelectionGeneration.current
    await navigate({
      resetScroll: !revealWorkspace.current,
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
  const scheduleSearchNavigation = (delay = searchNavigationDelay) => {
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
              replace: pending.attention === undefined,
              resetScroll: !revealWorkspace.current,
              search: (current) => {
                if (generation !== searchGeneration.current) return current
                const next = { ...current }
                if (pending.q !== null) {
                  delete next.cursor
                  delete next.attentionCursor
                  if (pending.q) next.q = pending.q
                  else delete next.q
                }
                if (pending.attention === true) next.attention = 'open'
                else if (pending.attention === false) delete next.attention
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
    }, delay)
  }
  const prefetchTimer = useRef<number | null>(null)
  useEffect(
    () => () => {
      if (prefetchTimer.current !== null) window.clearTimeout(prefetchTimer.current)
    },
    [],
  )
  const prefetchOnIntent = <TData, TKey extends QueryKey>(
    options: QueryExecuteOptions<TData, Error, TData, TData, TKey>,
  ) => {
    if (prefetchTimer.current !== null) window.clearTimeout(prefetchTimer.current)
    prefetchTimer.current = window.setTimeout(() => {
      prefetchTimer.current = null
      prefetchBackgroundQuery(queryClient, options)
    }, prefetchIntentDelay)
  }
  const pageIntent = (cursor: string | undefined) => {
    if (cursor && !retained) prefetchOnIntent(libraryQuery(http, { ...search, cursor }))
  }
  const workIntent = (work: LibraryWorkView) => {
    if (work.reviewCaseId && !retained) prefetchOnIntent(detailQuery(http, work.reviewCaseId))
  }
  const lensIntent = (lens: LibrarySearch['lens']) =>
    prefetchOnIntent(libraryQuery(http, { lens, ...(search.q ? { q: search.q } : {}) }))
  const runSearch = (value: string) => {
    const q = value.trim()
    setTypedQuery({ echo: q, text: value })
    setSearchPending(true)
    pendingSearchQuery.current = { ...pendingSearchQuery.current, q }
    scheduleSearchNavigation()
  }
  const closeAttention = () => {
    returnToToggle.current = true
    returnToWork.current = Boolean(search.preview ?? search.work) && narrowWidth()
    changeAttention(false)
  }
  const changeAttention = (open: boolean) => {
    // Only a narrow rail covers the work; from 761px it overlays the page,
    // which stays where it was with the toggle in view.
    if (!open) revealWorkspace.current = Boolean(search.preview ?? search.work) && narrowWidth()
    pendingSearchQuery.current = {
      q: searchPending ? queryText.trim() : null,
      attention: open,
    }
    scheduleSearchNavigation(0)
  }
  const submitSearch = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault()
    runSearch(queryText)
  }
  // A retained page's cursors belong to the key the URL just left.
  const movePage = (cursor: string | undefined) => {
    if (retained) return
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
    const idempotencyKey = requestKey()
    setOwnerCommand(null)
    setOwnerNotice(null)
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
      idempotencyKey,
      ...(field.coverConsideration
        ? {
            considerationId: field.coverConsideration.id,
            coverConfirmation: {
              expected_selection_id: field.currentCoverSelectionId ?? null,
              confirm_unverified_match:
                outcome === 'accept' &&
                field.coverConsideration.candidates.some(
                  (candidate) =>
                    candidate.artifactId === selectedValue?.value &&
                    candidate.matchState === 'match_not_confirmed',
                ),
            },
          }
        : {}),
      ...(authoredDescription ? { authoredDescription } : {}),
    })
    return idempotencyKey
  }
  const closeAutomaticPanel = () => {
    const closingKey = automaticPanel
    restoreAutomaticTrigger.current = true
    setAutomaticPanel(null)
    if (closingKey && ['error', 'refreshed'].includes(automaticResults[closingKey] ?? ''))
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
    revealWorkspace.current = false
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
    if (attention.isPlaceholderData) return
    revealWorkspace.current = compactWidth()
    revealQueueChoice.current = true
    workSelectionGeneration.current += 1
    if (reviewCaseId !== selectedReviewCaseId)
      setExpandedChoiceState({ work: reviewCaseId, keys: new Set() })
    setComplete(null)
    setCompletionInspection(null)
    setOpenedWork(null)
    void navigate({
      resetScroll: !revealWorkspace.current,
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
      (selected.destination === 'cover' && Boolean(selected.currentCoverSelectionId)) ||
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
    (selected.decisionActions.length > 0 ||
      Boolean(selected.ownerControl?.entry ?? selected.ownerControl?.subset))
  const selectedOwnerEntry = Boolean(
    selected?.ownerControl?.entry ?? selected?.ownerControl?.subset,
  )
  const ownerKey = selected ? `${selectedReviewCaseId}:${selected.destination}` : null
  const ownerStatusShown = ownerKey !== null && ownerCommand === ownerKey
  const selectedOptional =
    selected !== undefined && !selectedActionable && !selectedRecorded && optionalChoice(selected)
  const selectedAdvice = selected?.destination === 'description' ? shownAdvice(selected) : null
  const selectedChoiceMode =
    selectedActionable || selectedRevisitable || selectedOptional || Boolean(selectedAdvice)
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
  // An owner command says what it did in its own status, once (S4).
  const choiceProgress =
    selectedProgress && ownerOutcome(selectedProgress.command.outcome) ? null : selectedProgress
  const teachingLine =
    choiceProgress?.phase === 'recording'
      ? 'Recording choice…'
      : choiceProgress?.phase === 'confirmed' ||
          (!selectedProgress && selected && authoredOwnerRecorded(selected))
        ? 'Choice recorded.'
        : selectedActionable && frozenDestinations.indexOf(selected.destination) === 0
          ? 'One tap records and opens the next field'
          : ''
  const writesActive = detail.data?.writesConsented === true
  // The direct choices in reading order, so the radio group's one Tab stop and
  // each position come from a single list (#1290).
  type DirectChoice = Omit<Parameters<typeof MetadataChoice>[0], 'radio'>
  const currentChoice: DirectChoice | null = selected?.decisionActions.includes('keep_current')
    ? {
        context: choiceContext(selected, null),
        current: true,
        disabled: false,
        expanded: expandedChoices.has(`${selected.destination}:current`),
        label: 'Current value',
        longDescription: isLongDescription(selected.destination, selected.currentValue),
        onChoose: () => choose(selected, 'keep_current'),
        recording:
          commandSelected(
            selectedProgress?.command ?? null,
            selected.destination,
            'keep_current',
          ) && selectedProgress?.phase === 'recording',
        selected: choiceSelected(selected, selectedProgress, 'keep_current'),
        toggle: () => toggleChoiceEvidence(`${selected.destination}:current`),
        value: valueText(selected.destination, selected.currentValue),
      }
    : null
  const proposedChoices: (DirectChoice & { id: string })[] =
    selected && selected.decisionActions.includes('accept') && !selected.preservationOnly
      ? selected.proposedValues.map((value, index) => {
          const context = choiceContext(
            selected,
            selected.choiceContexts.filter((item) => item.selection === 'proposed')[index]
              ?.alternativeId ?? null,
          )
          const key = `${selected.destination}:${context?.alternativeId ?? String(index)}`
          return {
            id: key,
            context,
            current: false,
            disabled: false,
            // A proposed card is named by its value, never "Alternative N" (#1519).
            evidenceName: isLongDescription(selected.destination, displayValue(value.value))
              ? `Evidence for proposed description ${String(index + 1)}`
              : `Evidence for proposed value: ${valueText(selected.destination, displayValue(value.value))}`,
            expanded: expandedChoices.has(key),
            label: '',
            longDescription: isLongDescription(selected.destination, displayValue(value.value)),
            onChoose: () => choose(selected, 'accept', value),
            recording:
              commandSelected(
                selectedProgress?.command ?? null,
                selected.destination,
                'accept',
                value,
              ) && selectedProgress?.phase === 'recording',
            selected: choiceSelected(selected, selectedProgress, 'accept', value),
            toggle: () => toggleChoiceEvidence(key),
            value: valueText(selected.destination, displayValue(value.value)),
          }
        })
      : []
  const retainedAdviceChoice: (DirectChoice & { id: string })[] =
    selectedAdvice?.selected_advice &&
    selectedAdvice.selected_advice.candidate.candidate_id !== selectedAdvice.candidate?.candidate_id
      ? [
          {
            id: 'advice:retained',
            advice: selectedAdvice,
            retainedAdvice: true,
            context: undefined,
            current: false,
            disabled: true,
            expanded: false,
            label: 'Advised · Authored content · Recorded choice',
            longDescription: isLongDescription(
              'description',
              selectedAdvice.selected_advice.candidate.output.description,
            ),
            onChoose: () => undefined,
            recording: false,
            selected: Boolean(
              selectedAdvice.selected_advice.current || selectedAdvice.application_outcome,
            ),
            toggle: () => undefined,
            value: selectedAdvice.selected_advice.candidate.output.description,
          },
        ]
      : []
  const adviceCandidate = selectedAdvice?.candidate
  const adviceChoice: (DirectChoice & { id: string })[] =
    selected && selectedAdvice && adviceCandidate
      ? [
          {
            id: 'advice:candidate',
            advice: selectedAdvice,
            retainedAdvice: selectedAdvice.selection?.candidate_id === adviceCandidate.candidate_id,
            context: undefined,
            current: false,
            disabled:
              !selected.editable ||
              !selectedAdvice.current ||
              controller.recording ||
              selectedAdvice.state === 'requested' ||
              selectedAdvice.state === 'running',
            expanded: false,
            label: 'Advised · Authored content',
            longDescription: isLongDescription('description', adviceCandidate.output.description),
            onChoose: () => {
              if (!selectedAdvice.current || !selected.editable) return
              choose(
                selected,
                'accept',
                { kind: 'text', value: adviceCandidate.output.description },
                { candidateId: adviceCandidate.candidate_id, binding: selectedAdvice.binding },
              )
            },
            recording:
              selectedProgress?.command.authoredDescription?.candidateId ===
                adviceCandidate.candidate_id && selectedProgress.phase === 'recording',
            selected:
              selectedProgress && selectedProgress.phase !== 'failed'
                ? selectedProgress.command.authoredDescription?.candidateId ===
                  adviceCandidate.candidate_id
                : Boolean(
                    (selectedAdvice.selected_advice?.current === true ||
                      selectedAdvice.application_outcome) &&
                    selectedAdvice.selection?.candidate_id === adviceCandidate.candidate_id,
                  ),
            toggle: () => undefined,
            value: adviceCandidate.output.description,
          },
        ]
      : []
  const newChoices = [...proposedChoices, ...retainedAdviceChoice, ...adviceChoice]
  const directChoices = choiceRadios(
    [...(currentChoice ? [{ ...currentChoice, id: 'current' }] : []), ...newChoices],
    (choice) => ({ checked: choice.selected, disabled: choice.disabled }),
  )

  return (
    <div className="library-page variant-m-workspace">
      <h1 className="sr-only">Library</h1>
      {/* One toolbar in both states, so selecting a work never moves the workspace. */}
      <header
        className="library-header"
        aria-label="Library controls"
        tabIndex={-1}
        {...(!hasSelection ? { 'data-route-focus': 'true' } : {})}
      >
        <button
          ref={attentionToggle}
          className="attention-toggle"
          type="button"
          aria-expanded={search.attention === 'open'}
          onClick={() => changeAttention(search.attention !== 'open')}
        >
          Attention
          {summaryAttention !== undefined || !summary.isError ? (
            <>
              {' '}
              <strong>{summaryAttention === undefined ? '…' : workCount(summaryAttention)}</strong>
            </>
          ) : null}
        </button>
        <WriteReadyNotice />
      </header>
      <PreparationNotice routeIdentity={routeIdentity} />
      <ProviderThrottleNotice />
      <div
        className={`library-workspace${hasSelection ? ' has-selection' : ''}${search.attention === 'open' ? ' attention-open' : ''}`}
      >
        {hasSelection && search.attention !== 'open' ? (
          <button className="compact-workspace-back" type="button" onClick={clearSelection}>
            Back to works
          </button>
        ) : null}
        <section className="library-works" aria-labelledby="library-works-title">
          {presentation ? (
            <PreparationSummary
              presentation={presentation}
              reconnecting={preparationState.reconnecting}
              recoveryRequired={preparationState.data?.recovery_required ?? false}
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
            <span role="status" aria-live="polite" aria-busy={retained || undefined}>
              {data ? (
                search.q ? (
                  <>
                    {lensTotal === undefined
                      ? `Found ${workCount(data.counts[lensCount])}`
                      : `Found ${String(data.counts[lensCount])} of ${workCount(lensTotal)}`}
                    {lensWords === undefined ? null : (
                      <span className="sr-only">{` ${lensWords}`}</span>
                    )}
                  </>
                ) : (
                  workCount(data.counts.all) +
                  // The works outside both decision lenses, named beside the
                  // total in the masthead's words (#1407, #1413).
                  (
                    [
                      [summaryNotMatched, 'not matched'],
                      [summaryFailed, 'could not finish'],
                    ] as const
                  )
                    .map(([count, words]) =>
                      count === undefined
                        ? summary.isError
                          ? ''
                          : ` · … ${words}`
                        : count === 0
                          ? ''
                          : ` · ${String(count)} ${words}`,
                    )
                    .join('')
                )
              ) : failed ? (
                'Count unavailable'
              ) : (
                'Loading count…'
              )}
            </span>
          </div>
          <nav className="lenses" aria-label="Library lenses" ref={lensRow}>
            {(
              [
                'all',
                'needs_attention',
                'no_attention_needed',
                'not_matched',
                'preparation_failed',
              ] as const
            ).map((lens) => (
              <LensLink
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
                onPointerEnter={() => lensIntent(lens)}
                onFocus={() => lensIntent(lens)}
                activeProps={{ 'aria-current': 'page' }}
              >
                {lensLabels[lens]}
              </LensLink>
            ))}
          </nav>
          <form className="search library-search" role="search" onSubmit={submitSearch}>
            <label className="sr-only" htmlFor="library-query">
              Search title, author, identifier, library, or format
            </label>
            <div>
              <input
                ref={searchInput}
                id="library-query"
                name="q"
                type="search"
                maxLength={128}
                value={queryText}
                placeholder="Search works…"
                onChange={(event) => runSearch(event.target.value)}
              />
              {queryText ? (
                <button
                  type="button"
                  aria-label="Clear search text"
                  onClick={() => {
                    runSearch('')
                    searchInput.current?.focus()
                  }}
                >
                  <span aria-hidden="true">×</span>
                </button>
              ) : null}
            </div>
          </form>
          <div className="library-work-list" aria-busy={retained || undefined}>
            {!data ? (
              <div className="empty-copy" aria-busy={!failed}>
                {failed ? (
                  <>
                    <p role="alert">Works are unavailable.</p>
                    <button type="button" onClick={retry}>
                      Try again
                    </button>
                  </>
                ) : (
                  <p role="status">Loading works…</p>
                )}
              </div>
            ) : works.length === 0 ? (
              <div className="empty-copy">
                <p>No exact-current works match this search and lens.</p>
                {search.q ? (
                  <button
                    type="button"
                    onClick={() => {
                      runSearch('')
                      searchInput.current?.focus()
                    }}
                  >
                    Clear search
                  </button>
                ) : null}
              </div>
            ) : (
              works.map((work, index) => {
                const selectedWorkRow =
                  !preparationSelected &&
                  (selectedWork
                    ? selectedWork.workId === work.workId
                    : work.reviewCaseId !== undefined && search.preview === work.reviewCaseId)
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
                // A terminal preparation failure outranks a retained report's
                // not-matched reason: it is the failure the owner must see (#1407).
                const status =
                  (work.summary && !work.preparation
                    ? notMatchedReasons[work.summary.state]
                    : undefined) ??
                  (recorded
                    ? 'Recorded evidence available'
                    : work.preparation
                      ? 'Preparation failed safely'
                      : needsAttention
                        ? work.attentionCount === 1
                          ? '1 field needs attention'
                          : `${String(work.attentionCount)} fields need attention`
                        : 'No attention needed')
                const terminalFailure = work.preparation !== null && !work.reviewCaseId && !evidence
                const group = providerGroup(work)
                const previousWork = works[index - 1]
                // Grouping follows the lens these rows were read for, not a retained URL's.
                const showGroup =
                  data.lens !== 'no_attention_needed' &&
                  data.lens !== 'not_matched' &&
                  data.lens !== 'preparation_failed' &&
                  (!previousWork || providerGroup(previousWork) !== group)
                const summary = providerSummary(work.preparationOutcomes)
                const summaryId = summary ? `provider-summary-${work.id}` : undefined
                const preparation = work.preparation
                const preparationId = preparation ? `preparation-${work.id}` : undefined
                const row = (
                  <>
                    <BookMark title={work.title} cover={work.cover} />
                    <span className="library-work-copy">
                      <strong className="library-work-title">{work.title}</strong>
                      <span className="library-work-author">{authorLine(work)}</span>
                      <span className="library-work-origin">
                        {work.libraryDisplayName ?? work.library} ·{' '}
                        {mediaKindLabels[work.mediaKind]}
                      </span>
                      {summary ? (
                        <span id={summaryId} className="library-provider-summary">
                          {summary}
                        </span>
                      ) : null}
                      <span className="library-work-status">
                        <StatusPill
                          tone={
                            !recorded && (needsAttention || work.preparation) ? 'notice' : 'accent'
                          }
                        >
                          {status}
                        </StatusPill>
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
                        {group === 'Provider evidence complete' ||
                        group === 'Provider evidence limited' ? (
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
                          aria-disabled={retained || undefined}
                          aria-label={`${work.title}. ${authorLine(work)}. ${status}`}
                          aria-describedby={
                            [preparationId, summaryId].filter(Boolean).join(' ') || undefined
                          }
                          onClick={() => void select(work)}
                          onPointerEnter={() => workIntent(work)}
                          onFocus={() => workIntent(work)}
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
          {data && (data.previousCursor || data.nextCursor) ? (
            // Only the moves that exist are offered; with one page there is no pager.
            <div className="library-pager" aria-busy={retained || undefined}>
              {data.previousCursor ? (
                <button
                  type="button"
                  className="library-pager-previous"
                  aria-disabled={retained || undefined}
                  onClick={() => movePage(data.previousCursor)}
                  onPointerEnter={() => pageIntent(data.previousCursor)}
                  onFocus={() => pageIntent(data.previousCursor)}
                >
                  ← <span className="library-pager-word">Previous</span>
                </button>
              ) : null}
              <span>Page {String(data.page)}</span>
              {data.nextCursor ? (
                <button
                  type="button"
                  className="library-pager-next"
                  aria-disabled={retained || undefined}
                  onClick={() => movePage(data.nextCursor)}
                  onPointerEnter={() => pageIntent(data.nextCursor)}
                  onFocus={() => pageIntent(data.nextCursor)}
                >
                  <span className="library-pager-word">Next</span> →
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
        <section
          className="workspace-detail"
          // Preparation states its own progress in one status line; the whole
          // detail would otherwise be read again on every poll (#1288).
          aria-live={preparationSelected ? undefined : 'polite'}
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
              recoveryRequired={preparationState.data?.recovery_required ?? false}
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
          ) : !displayedIdentity && staleCoverNotice ? (
            staleCoverNotice
          ) : !displayedIdentity ? (
            identityPending || leaving || successorPending === search.preview ? (
              <p role="status">Loading selected work…</p>
            ) : identityError ? (
              <p role="alert">
                Selected work could not be loaded.{' '}
                <button
                  ref={selectedDetailRetryButton}
                  onClick={() => {
                    selectedDetailRetry.current = search.preview ?? null
                    retry()
                    void attention.refetch()
                    // The case read names the work too, so it is read again (#1365).
                    if (detail.isError) void detail.refetch()
                  }}
                >
                  Try again
                </button>
              </p>
            ) : (
              <div className="library-selection">
                <h2
                  tabIndex={-1}
                  // Reached when the case under an open owner form could not
                  // be followed: focus lands here, never on the page body.
                  ref={(heading) => {
                    if (heading && document.activeElement === document.body)
                      heading.focus({ preventScroll: true })
                  }}
                >
                  Selected work
                </h2>
                <p>Readable work identity is not available on this Library page.</p>
                <p role="status">This work is not available in the current Library result.</p>
              </div>
            )
          ) : detail.isPending || (detail.isError && leaving) ? (
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
                <BookMark
                  title={displayedIdentity.title}
                  cover={
                    currentWorks.find((work) => work.reviewCaseId === selectedReviewCaseId)?.cover
                  }
                />
                <div>
                  <h2>{displayedIdentity.title}</h2>
                  <p>
                    {displayedIdentity.authors.length
                      ? displayedIdentity.authors.join(', ')
                      : 'Author details unavailable'}{' '}
                    · {displayedIdentity.libraryDisplayName ?? displayedIdentity.library} ·{' '}
                    {mediaKindLabels[displayedIdentity.mediaKind]}
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
                            field.humanDecision === null &&
                            field.automaticDecision !== null &&
                            !field.manualHold,
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
                      <strong>{destinationLabel(field.destination)}</strong>{' '}
                      <span className={`field-status is-${fieldStatus(field)[0]}`}>
                        {fieldStatus(field)[1]}
                      </span>
                    </button>
                  ))}
                  {omittedFields.length ? (
                    <button
                      type="button"
                      className="field-more"
                      onClick={() => {
                        setRevealFocusDestination(omittedFields[0]?.destination ?? null)
                        setShownEmptyFields((current) => ({
                          ...current,
                          [selectedReviewCaseId]: true,
                        }))
                      }}
                    >
                      {`Show ${String(omittedFields.length)} more field${omittedFields.length === 1 ? '' : 's'}`}
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
                    <h3>Review complete</h3>
                    <span>
                      {String(complete.fields.length)} recorded field outcome
                      {complete.fields.length === 1 ? '' : 's'}
                      {writesActive ? ' — each began applying the moment you decided it.' : '.'}
                    </span>
                  </div>
                  <ol>
                    {complete.fields.map((field) => (
                      <li key={field.destination}>
                        <button
                          type="button"
                          aria-label={
                            field.destination === 'description' && decisionValue(field).length > 240
                              ? `Revisit description: ${completionOutcome(field)}`
                              : undefined
                          }
                          onClick={() => {
                            setCompletionInspection(field.destination)
                            selectCase(selectedReviewCaseId, field.destination)
                          }}
                        >
                          <small>
                            {destinationLabel(field.destination)} · {completionOutcome(field)}
                          </small>
                          <strong>{decisionValue(field)}</strong>
                        </button>
                      </li>
                    ))}
                  </ol>
                  {writesActive ? <p>Every write is logged in Activity.</p> : null}
                  <footer>
                    {nextWork ? (
                      <Button
                        variant="primary"
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
                      </Button>
                    ) : attention.isPending ||
                      attention.isPlaceholderData ||
                      attention.isFetchingNextPage ? (
                      <span>Looking for the next visible work…</span>
                    ) : nextWorkError ? (
                      <>
                        <span>The next attention work could not be loaded.</span>
                        <Button variant="primary" type="button" onClick={retryNextWork}>
                          Try finding next work again
                        </Button>
                      </>
                    ) : !search.q ? (
                      <span>All visible review work is complete.</span>
                    ) : (
                      <span>
                        All review work matching this search is complete.
                        {summaryAttention === undefined || !data
                          ? null
                          : outsideSearch > 0
                            ? ` ${String(outsideSearch)} more ${outsideSearch === 1 ? 'work needs' : 'works need'} a decision outside this search.`
                            : ' No other work needs a decision.'}
                      </span>
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
                      <h3>
                        {selectedActionable
                          ? decisionQuestion(selected.destination)
                          : destinationLabel(selected.destination)}
                      </h3>
                      {selectedActionable ? (
                        // One line under the question: this field's own reason (#1519).
                        <p
                          className={
                            selected.automaticWithheldReason
                              ? 'automatic-withheld-reason'
                              : undefined
                          }
                        >
                          {questionReason(selected, detail.data.target.mediaKind)}
                        </p>
                      ) : selectedRevisitable ? (
                        <p>
                          {selected.humanDecision === null && selected.manualHold
                            ? 'Your earlier decision still holds this field.'
                            : 'The recorded outcome is marked below.'}{' '}
                          You can select another eligible value.
                        </p>
                      ) : selectedOptional ? (
                        <p>{fieldReason(selected)}</p>
                      ) : null}
                      {selected.automaticWithheldReason && !selectedActionable ? (
                        <p className="automatic-withheld-reason">
                          {selected.automaticWithheldReason}
                        </p>
                      ) : null}
                    </div>
                    {selectedActionable ? (
                      <dl>
                        <div>
                          <dt>Queue position</dt>
                          <dd>
                            {activeQueueGroup >= 0
                              ? `Work ${String(activeQueueGroup + 1)} of ${String(attentionGroups.length)}${attention.hasNextPage || search.attentionCursor ? ' loaded' : ''}`
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
                        // The radio group inside is named by the question.
                        aria-label={
                          selectedActionable
                            ? undefined
                            : selectedOptional
                              ? `${destinationLabel(selected.destination)} choices`
                              : `${destinationLabel(selected.destination)} recorded choices`
                        }
                      >
                        {selectedRevisitable ? (
                          <>
                            {combinedAutomaticTags(selected, detail.data.target.mediaKind) ||
                            selectedAutomaticLanguageForm(selected) ? (
                              <RecordedOutcome
                                field={selected}
                                mediaKind={detail.data.target.mediaKind}
                                expanded={expandedChoices.has(`${selected.destination}:recorded`)}
                                toggle={() =>
                                  toggleChoiceEvidence(`${selected.destination}:recorded`)
                                }
                              />
                            ) : null}
                            <ProviderConsensusAutomaticStatement field={selected} />
                            <DescriptionAutomaticStatement field={selected} />
                            <SeriesAutomaticStatement field={selected} />
                            <NarratorsAutomaticStatement field={selected} />
                            <AsinAutomaticStatement field={selected} />
                            <VerifiedManifestationIsbnAutomaticStatement field={selected} />
                          </>
                        ) : null}
                        <div
                          className={`direct-choice-grid${selected.preservationOnly ? ' preservation-only' : ''}`}
                          role="radiogroup"
                          aria-label={decisionQuestion(selected.destination)}
                          onKeyDown={moveChoiceFocus}
                        >
                          {directChoices
                            .filter((choice) => choice.current)
                            .map(({ id, ...choice }) => (
                              <MetadataChoice key={id} {...choice} />
                            ))}
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
                              {directChoices
                                .filter((choice) => !choice.current)
                                .map(({ id, ...choice }) => (
                                  <MetadataChoice key={id} {...choice} />
                                ))}
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
                      mediaKind={detail.data.target.mediaKind}
                      expanded={expandedChoices.has(`${selected.destination}:recorded`)}
                      toggle={() => toggleChoiceEvidence(`${selected.destination}:recorded`)}
                    />
                  ) : (
                    <>
                      {selectedActionable || !selectedOwnerEntry ? (
                        <p
                          className={selectedActionable ? 'decision-recorded' : 'decision-context'}
                        >
                          {selectedActionable
                            ? `${fieldReason(selected)}${selected.humanDecision || selected.automaticDecision ? ` The recorded value remains ${decisionValue(selected)}.` : ''}`
                            : 'There’s no other value to choose from.'}
                        </p>
                      ) : null}
                      {!selectedActionable ? (
                        <ContextValue
                          field={selected}
                          expanded={expandedChoices.has(`${selected.destination}:context`)}
                          toggle={() => toggleChoiceEvidence(`${selected.destination}:context`)}
                        />
                      ) : null}
                    </>
                  )}
                  {selected.ownerControl && !controller.reloadRequired ? (
                    <OwnerControls
                      key={ownerKey}
                      field={selected}
                      disabled={controller.recording || controller.reloadRequired}
                      progress={selectedProgress}
                      status={
                        ownerNotice?.caseId === selectedReviewCaseId &&
                        ownerNotice.destination === selected.destination
                          ? ownerNotice.text
                          : ownerStatusShown
                            ? controller.statusFor(selected.destination)
                            : ''
                      }
                      failed={controller.decision.isError}
                      submit={(outcome, value) => {
                        const key = choose(selected, outcome, value)
                        setOwnerCommand(ownerKey)
                        return key
                      }}
                    />
                  ) : null}
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
                          <h4>Automatic handling is paused for this field</h4>
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
                          ) : automaticResult === 'refreshed' ? (
                            <p role="alert">{sessionRefreshedCopy}</p>
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
                        <>
                          {selected.humanDecision === null ? (
                            <p>
                              An earlier owner decision is pausing automatic handling until you
                              choose Resume automatic handling.
                            </p>
                          ) : null}
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
                        </>
                      )}
                    </section>
                  ) : null}
                  {comparisonNotices(selected).length ? (
                    <aside className="comparison-notice" aria-label="Comparison condition">
                      {comparisonNotices(selected).map((notice) => (
                        <p key={notice}>
                          {noticeText(selected, notice, detail.data.target.mediaKind)}
                        </p>
                      ))}
                    </aside>
                  ) : null}
                  <RecordedFieldObservations
                    key={selectedReviewCaseId}
                    reviewCaseId={selectedReviewCaseId}
                    field={selected}
                  />
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
                  !ownerStatusShown &&
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
                  {controller.reloadRequired ? (
                    <button
                      type="button"
                      disabled={detail.isFetching}
                      onClick={() => void controller.settle()}
                    >
                      {detail.isFetching ? 'Reloading current case…' : 'Reload current case'}
                    </button>
                  ) : null}
                </article>
              ) : (
                <p>This work has no fields requiring a decision.</p>
              )}
              {detail.data.recordingIdentityBasis || detail.data.audiobookReviewScope === 'work' ? (
                <section aria-label="Whole-work context">
                  <h3>Whole-work context</h3>
                  <RecordingIdentityBasis
                    value={detail.data.recordingIdentityBasis}
                    scope={detail.data.audiobookReviewScope}
                    offered={detail.data.fields
                      .filter(railOffers)
                      .map((field) => field.destination)}
                  />
                </section>
              ) : null}
              {contextualFields.length ? (
                <details className="automatic-resolution-details">
                  <summary>
                    <span>
                      {contextualSummary.unchanged.length ? (
                        <span>
                          No change:{' '}
                          {contextualSummary.unchanged
                            .map((field) => destinationLabel(field.destination))
                            .join(', ')}
                        </span>
                      ) : null}
                      {contextualSummary.waiting ? (
                        <span>{heldPhrase(contextualSummary.waiting, 'waiting')}</span>
                      ) : null}
                      {contextualSummary.unavailable ? (
                        <span>{heldPhrase(contextualSummary.unavailable, 'unavailable')}</span>
                      ) : null}
                    </span>
                    <small>Show why</small>
                  </summary>
                  <div className="automatic-resolution-list">
                    {contextualFields.map((field) => {
                      const kind = contextKind(field)
                      return (
                        <div
                          className={`automatic-resolution-row is-${kind}`}
                          key={field.destination}
                        >
                          <strong>
                            {destinationLabel(field.destination)}
                            {kind === 'unchanged' ? null : (
                              <StatusPill tone="notice">
                                {kind === 'waiting' ? 'Waiting' : 'Unavailable'}
                              </StatusPill>
                            )}
                          </strong>
                          <small>{decisionValue(field)}</small>
                          <small>
                            {kind === 'unchanged'
                              ? 'No source offered a different value.'
                              : fieldReason(field)}
                          </small>
                        </div>
                      )
                    })}
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
            aria-busy={attention.isPlaceholderData || undefined}
            tabIndex={-1}
            ref={attentionRail}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault()
                closeAttention()
                return
              }
              if (event.key !== 'Tab' || typeof window.matchMedia !== 'function') return
              if (!window.matchMedia(drawerQuery).matches) return
              const stops = [
                ...event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input'),
              ].filter((stop) => !stop.matches(':disabled') && stop.getClientRects().length)
              const edge = event.shiftKey ? stops[0] : stops.at(-1)
              const active = document.activeElement
              if (active === edge || (event.shiftKey && !stops.some((stop) => stop === active))) {
                event.preventDefault()
                ;(event.shiftKey ? stops.at(-1) : stops[0])?.focus()
              }
            }}
          >
            <header>
              <div>
                <h2 ref={attentionHeading} tabIndex={-1}>
                  Attention
                </h2>
                <p>
                  {search.q && !data
                    ? failed
                      ? 'Count unavailable'
                      : 'Counting works…'
                    : summaryAttention === undefined
                      ? !summary.isError
                        ? 'Counting works…'
                        : search.q
                          ? `Matching: ${workCount(data?.counts.attention ?? 0)} needing a decision`
                          : 'Count unavailable'
                      : !search.q
                        ? `${workCount(summaryAttention)} ${summaryAttention === 1 ? 'needs' : 'need'} a decision`
                        : `Matching: ${String(data?.counts.attention ?? 0)} of ${workCount(summaryAttention)} needing a decision`}
                </p>
              </div>
              <button onClick={closeAttention}>Close</button>
            </header>
            {attention.data === undefined ? (
              <p role={attention.isError ? 'alert' : 'status'}>
                {attention.isError ? (
                  <>
                    Attention could not be loaded.{' '}
                    <button
                      ref={attentionRetry}
                      type="button"
                      onClick={() =>
                        void attention.refetch().then((result) => {
                          // The retry removes this button while it reads, so
                          // the rail's heading takes focus back on success
                          // (#1399) and Try again does when it fails again.
                          const active = document.activeElement
                          if (
                            active &&
                            active !== document.body &&
                            !attentionRail.current?.contains(active)
                          )
                            return
                          if (result.isSuccess)
                            attentionHeading.current?.focus({ preventScroll: true })
                          else
                            requestAnimationFrame(() =>
                              attentionRetry.current?.focus({ preventScroll: true }),
                            )
                        })
                      }
                    >
                      Try again
                    </button>
                  </>
                ) : (
                  'Loading attention…'
                )}
              </p>
            ) : (
              <>
                {attentionGroups.map((group, index) => {
                  const work = group[0]
                  if (!work) return null
                  return (
                    <div key={work.id} className="attention-work-group">
                      <div className="attention-work-identity">
                        <span className="attention-position">{String(index + 1)}</span>
                        <span>
                          <strong>{work.title}</strong>
                          <small>
                            {work.authors.length
                              ? work.authors.join(', ')
                              : 'Author details unavailable'}
                          </small>
                          <small>
                            {work.libraryDisplayName ?? work.library} ·{' '}
                            {mediaKindLabels[work.mediaKind]}
                          </small>
                        </span>
                        <span className="attention-work-count">
                          {`${String(work.workAttentionCount)} ${work.workAttentionCount === 1 ? 'field' : 'fields'}`}
                        </span>
                      </div>
                      <ul className="attention-field-rows">
                        {group.map((item) => {
                          const line = `${destinationLabel(item.destination)} · ${item.classificationReason ?? 'Needs your decision'}`
                          return (
                            <li key={item.destination}>
                              <button
                                className="attention-queue-item"
                                // The same field in two works would otherwise share a name in a
                                // screen reader's list of buttons; the work is shown above (#1361).
                                aria-label={`${line}, ${work.title}`}
                                aria-current={
                                  item.id === search.preview &&
                                  item.destination === selected?.destination
                                }
                                aria-disabled={attention.isPlaceholderData || undefined}
                                data-attention-row={`${item.id}:${item.destination}`}
                                onClick={() =>
                                  selectFromQueue(item.id, item.destination, item.attentionCursor)
                                }
                              >
                                <span className="attention-queue-copy">
                                  <b>{line}</b>
                                </span>
                              </button>
                            </li>
                          )
                        })}
                      </ul>
                    </div>
                  )
                })}
                {attention.hasNextPage ? (
                  <button
                    className="attention-load-more"
                    type="button"
                    aria-disabled={attention.isFetchingNextPage || undefined}
                    onClick={() => {
                      if (loadMore.current || attention.isFetchingNextPage) return
                      loadMore.current = {
                        key: attentionKey,
                        pages: attention.data.pages.length,
                        updated: attentionQuery.dataUpdatedAt,
                        errors: attentionQuery.errorUpdateCount,
                        from: attentionItems.length,
                      }
                      void attention.fetchNextPage()
                    }}
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
