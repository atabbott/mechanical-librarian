import { Link, useNavigate } from '@tanstack/react-router'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import { useId, useLayoutEffect, useRef } from 'react'
import type { SyntheticEvent } from 'react'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { CoverObservationView, CoverTasteSuggestion, DecisionCommand } from '../api/http'
import { choiceRadios, moveChoiceFocus } from '../choiceGroup'
import { useCoverChoiceData } from '../coverChoices'
import type { CoverPrepareFailure, RecordCoverPrepareFailure } from '../coverChoices'
import {
  coverFaultLabel,
  coverNoteLabel,
  coverReviewerLabel,
  coverSourceLabels,
  reviewerFailureCopy,
} from '../labels'
import { useReviewContext } from '../router'
import { KeyBadge } from './ReviewKeys'
import { Button } from '../components/Button'
import { StatusPill } from '../components/StatusPill'
import { tasteSectionHeadings } from '../tasteSections'
import type { TasteSection } from '../tasteSections'
import { SettingsPathText } from '../components/SettingsPathText'

import {
  coverNames,
  commandSelected,
  choiceSelected,
  decisionQuestion,
  audioFileFormat,
} from '../fieldDecision'
import type { ReviewField, ChoiceProgress } from '../fieldDecision'
import { ChoiceEvidencePanel } from './FieldDecisionChoices'
// The press that failed picks the copy, whichever layout the reload shows.
const coverPrepareFailureCopy = ({ action, error }: CoverPrepareFailure) =>
  action === 'prepare'
    ? uncertainWriteOutcome(error)
      ? 'No answer came back. Check whether the cover comparison was prepared before preparing it again.'
      : refusalCopy(error, 'The cover comparison could not be prepared. Nothing was changed.')
    : uncertainWriteOutcome(error)
      ? 'No answer came back. Check whether the visual review restarted before retrying it.'
      : refusalCopy(error, 'The visual review could not be retried. Nothing was changed.')
const reviewerUnavailableDetail = (reason: string | null) =>
  reason === 'profile contract requires memory_bytes=1073741824'
    ? 'The reviewer profile must set memory_bytes to 1073741824. Generate a new profile, replace the configured one, then restart the service.'
    : reason === 'profile contract requires process_limit=32'
      ? 'The reviewer profile must set process_limit to 32. Generate a new profile, replace the configured one, then restart the service.'
      : null
// Only keyboard focus reveals a card. Focus from a pointer arrives on
// mousedown, so scrolling then moved the card from under the press and lost
// the tap at compact widths (#1290).
const revealFocusedCoverChoice = (event: SyntheticEvent<HTMLElement>) => {
  if (!(event.target instanceof Element) || !event.target.matches(':focus-visible')) return
  const choice = event.currentTarget
  const track = choice.closest<HTMLElement>('.library-cover-layout, .library-cover-pinned')
  if (!track) return
  const trackBounds = track.getBoundingClientRect()
  const choiceBounds = choice.getBoundingClientRect()
  // Match the track's start snap points so snapping cannot clip the focused card.
  const delta =
    choiceBounds.left < trackBounds.left
      ? choiceBounds.left - trackBounds.left
      : choiceBounds.right > trackBounds.right
        ? choiceBounds.left - trackBounds.left
        : 0
  if (delta !== 0) track.scrollLeft += delta
}
// What the Covers view says differently from the book page (#1633): it asks
// about a book, names a current cover an automatic write chose, and is itself
// the Covers view, so it shows no link back to it.
interface CoverChoiceWording {
  question?: string
  currentLabel?: string
  /** The current cover card's accessible name: keeping it, in the label's words. */
  keepLabel?: string
  coversLink?: boolean
  /**
   * Covers' picks (#1372 S6): the owner says which cover they like, so the
   * cards are shuffled for the work in one grid, the current cover is marked
   * rather than first, and no recommendation, header or status shows.
   */
  calibration?: boolean
}
export function LibraryCoverChoices(
  props: {
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
    prepareFailure: { failure: CoverPrepareFailure | null; record: RecordCoverPrepareFailure }
  } & CoverChoiceWording,
) {
  return (
    <LibraryCoverChoicesContent
      key={`${props.reviewCaseId}:${props.field.coverConsideration?.id ?? 'absent'}`}
      {...props}
    />
  )
}
type CoverObservationValue = CoverObservationView[Exclude<
  keyof CoverObservationView,
  'candidateReference' | 'previewSha256' | 'previewVariant'
>]
const coverObservationLabel = (value: CoverObservationValue) =>
  (
    ({
      dense: 'Dense',
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
    }) satisfies Record<CoverObservationValue, string>
  )[value]
function CoverObservationEvidence({
  label,
  mediaKind,
  observation,
  reviewCompleted,
}: {
  label: string
  mediaKind: string
  observation: CoverObservationView | null
  reviewCompleted: boolean
}) {
  // A review that did not run is said once in the header; a card says only
  // that a completed review has no result for it (#1765).
  if (observation === null)
    return reviewCompleted ? (
      <section className="cover-observation is-unavailable" aria-label={`${label} visual checks`}>
        <strong>Visual checks unavailable</strong>
        <small>The visual review has no result for this cover.</small>
      </section>
    ) : null
  const expectedBranding = audioFileFormat(mediaKind)
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
              <li key={defect}>
                <StatusPill tone="danger">{defect}</StatusPill>
              </li>
            ))}
            {uncertain.map((item) => (
              <li key={item}>
                <StatusPill tone="notice">{item} uncertain</StatusPill>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <details className="cover-observation-details">
        <summary>Visual check details</summary>
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
type CoverConsideration = NonNullable<ReviewField['coverConsideration']>
type CoverCandidate = CoverConsideration['candidates'][number]
type CoverContext = CoverConsideration['choiceContexts'][number]
type CoverData = ReturnType<typeof useCoverChoiceData>
type ChooseCover = (
  outcome: DecisionCommand['outcome'],
  selectedValue?: DecisionCommand['selectedValue'],
) => void
type CoverRadio = ReturnType<typeof choiceRadios<unknown>>[number]['radio']
interface EvidenceToggle {
  expandedEvidence: { has: (key: string) => boolean }
  revealEvidence: (key: string) => void
}
// Whether the configured reviewer can be asked to review the covers again.
const useCanRetryReviewer = () => {
  const { http } = useReviewContext()
  const access = useQuery({
    queryKey: ['reviewer-access'] as const,
    queryFn: (context) => http.getReviewerAccess(context.signal, queryWorkClass(context)),
    retry: false,
  })
  const mapping = access.isSuccess ? access.data.mapping : null
  const currentReviewer = mapping && access.isSuccess ? access.data[mapping.engine] : null
  return (
    currentReviewer !== null &&
    [
      'ready',
      'configured',
      'usable',
      'failed',
      'interrupted',
      'unreachable',
      'malformed_response',
    ].includes(currentReviewer.state)
  )
}
// A contradiction says why the cover cannot be chosen, ahead of the matched
// book (#1790). Every other card the line reaches says Match not confirmed in
// its title, so no line repeats it (#1765).
const coverExcludedReason = (candidate: CoverCandidate) =>
  candidate.contradiction === 'book'
    ? 'A source says it belongs to a different book, so you cannot choose it.'
    : candidate.contradiction === 'edition'
      ? 'A source says it belongs to a different edition, so you cannot choose it.'
      : candidate.contradiction === 'creator'
        ? 'A source names a different author or contributor, so you cannot choose it.'
        : candidate.matchReason === 'matched_book'
          ? 'From the matched book, not a confirmed recording.'
          : candidate.matchReason === 'title_and_author' &&
              Array.isArray(candidate.identity?.reasons) &&
              candidate.identity.reasons.includes('creator_initials_folded')
            ? // The title names the basis, so only folded initials add a line (#1355).
              "An author's initials are written differently in this source."
            : null
// What a prepared comparison shows: its cards, the current cover's own
// sources, and the measured rules' stage and recommendation.
const coverComparisonShape = (
  consideration: CoverConsideration,
  mediaKind: string,
  suggestion: CoverTasteSuggestion | null = null,
) => {
  const candidates = consideration.candidates
  const incumbent = candidates.find((candidate) => candidate.incumbent)
  // A larger copy of the current picture is its own card, so it is neither a
  // merged duplicate nor one of the current cover's sources (#1363 M9).
  const largerCopies = candidates.filter((candidate) => candidate.candidateKind === 'larger_copy')
  const duplicates = consideration.omissions.filter(
    (omission) =>
      omission.reason === 'duplicate_preview' &&
      !largerCopies.some((candidate) => candidate.reference === omission.reference),
  )
  // With no other duplicate of it left, the current cover's sources are its own.
  const currentSourceKinds =
    incumbent === undefined
      ? []
      : largerCopies.length &&
          !duplicates.some((omission) => omission.retained_reference === incumbent.reference)
        ? incumbent.sourceKinds.filter(
            (kind) => !largerCopies.some((candidate) => candidate.sourceKinds.includes(kind)),
          )
        : incumbent.sourceKinds
  const policyStatus =
    typeof consideration.policy?.status === 'string' ? consideration.policy.status : null
  // The taste judge's choice, when it names a card here, is the suggested
  // card in place of the measured rules' (#1372 PR 4b).
  const taste =
    suggestion !== null &&
    candidates.some((candidate) => candidate.reference === suggestion.candidate_reference)
      ? suggestion
      : null
  const recommendedReference =
    taste !== null
      ? taste.candidate_reference
      : policyStatus === 'recommended' &&
          typeof consideration.policy?.recommended_candidate_reference === 'string'
        ? consideration.policy.recommended_candidate_reference
        : null
  // The measured rules' stage (#1363): over a populated cover its choice is a
  // suggestion, shown with the fault that prompted it; a fill is recommended.
  const stage = consideration.stage ?? null
  const audiobook = audioFileFormat(mediaKind) || mediaKind === 'audio'
  const stageReason = stage?.reason ?? null
  return {
    candidates,
    expanded: candidates.length > 3,
    incumbent,
    alternatives: candidates.filter((candidate) => !candidate.incumbent),
    duplicates,
    currentSourceKinds,
    recommendedReference,
    stage,
    stageReason,
    taste,
    suggesting:
      taste !== null ||
      (recommendedReference !== null &&
        (stage?.outcome === 'suggest' || stage?.outcome === 'replace')),
    faultLabel:
      stageReason !== null && stage?.outcome !== 'fill'
        ? coverFaultLabel(stageReason, audiobook)
        : null,
  }
}
type CoverShape = ReturnType<typeof coverComparisonShape>
// FNV-1a over 32 bits: one stable order for a work's cards, the same on every
// render and every read of the same case, and different from case to case.
const fnv1a = (text: string) => {
  let hash = 0x811c9dc5
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash
}
/** A comparison's shape as Covers' picks show it (S6). */
const calibrationShape = (shape: CoverShape, reviewCaseId: string): CoverShape => {
  const keyed = shape.candidates.map((candidate) => ({
    candidate,
    key: fnv1a(`${reviewCaseId}\u0000${candidate.reference}`),
  }))
  keyed.sort(
    (left, right) =>
      left.key - right.key || (left.candidate.reference < right.candidate.reference ? -1 : 1),
  )
  return {
    ...shape,
    candidates: keyed.map((item) => item.candidate),
    expanded: false,
    recommendedReference: null,
    taste: null,
    suggesting: false,
  }
}
// A card's choice: what it records, whether it is chosen, and whether it can
// be chosen now.
const coverCardState =
  ({
    field,
    progress,
    keeps,
    accepts,
    locked,
    imageFailed,
  }: {
    field: ReviewField
    progress: ChoiceProgress
    keeps: boolean
    accepts: boolean
    /** Every card is closed: the page is busy or the search is still running. */
    locked: boolean
    imageFailed: (reference: string) => boolean
  }) =>
  (candidate: CoverCandidate) => {
    const outcome = candidate.incumbent ? ('keep_current' as const) : ('accept' as const)
    const value: DecisionCommand['selectedValue'] = candidate.incumbent
      ? null
      : { kind: 'cover', value: candidate.artifactId }
    const selected =
      choiceSelected(field, progress, outcome, value) ||
      field.currentCoverSelectionArtifactId === candidate.artifactId
    const actionableCandidate = candidate.incumbent ? keeps : accepts && candidate.manualEligible
    return {
      outcome,
      value,
      selected,
      actionableCandidate,
      disabled: locked || !actionableCandidate || imageFailed(candidate.reference),
    }
  }
type CoverCardState = ReturnType<ReturnType<typeof coverCardState>>
// The cards in reading order, one radio group whose Tab stop and positions
// follow the direct choices' rules (#1290). An unreadable current cover's
// keep card comes first.
const coverRadioMap = (
  { expanded, incumbent, alternatives, candidates }: CoverShape,
  cardState: (candidate: CoverCandidate) => CoverCardState,
  unavailableCurrent: { shown: boolean; checked: boolean; disabled: boolean },
) =>
  new Map(
    choiceRadios(
      [
        ...(unavailableCurrent.shown ? [null] : []),
        ...(expanded && incumbent ? [incumbent, ...alternatives] : candidates),
      ],
      (candidate) => {
        if (candidate === null)
          return { checked: unavailableCurrent.checked, disabled: unavailableCurrent.disabled }
        const state = cardState(candidate)
        return {
          checked: state.actionableCandidate && state.selected,
          disabled: state.disabled,
        }
      },
    ).map(({ radio, ...candidate }) => [
      'reference' in candidate ? candidate.reference : 'current-unavailable',
      radio,
    ]),
  )
function PrepareCoverComparison({
  disabled,
  cover,
  failure,
}: {
  disabled: boolean
  cover: CoverData
  failure: CoverPrepareFailure | null
}) {
  return (
    <section aria-label="Prepare cover comparison">
      <p role="status">No cover comparison is ready.</p>
      <button
        type="button"
        disabled={disabled || cover.prepare.isPending}
        onClick={() => cover.prepare.mutate('prepare')}
      >
        Prepare cover comparison
      </button>
      {failure ? <p role="alert">{coverPrepareFailureCopy(failure)}</p> : null}
    </section>
  )
}
// A card's words: its label and choice, its match, size and sources, and why
// it can or cannot be chosen.
// The card's reason line: why it cannot be chosen, or why it is recommended.
// The card button names it in its description, so it is computed once (#1837).
const coverCardReason = (
  candidate: CoverCandidate,
  context: CoverContext | undefined,
  recommending: boolean,
): string | null =>
  !(candidate.incumbent || candidate.manualEligible) ||
  candidate.matchState === 'match_not_confirmed' ||
  candidate.matchReason === 'matched_book'
    ? coverExcludedReason(candidate)
    : recommending && context?.recommendationReason
      ? context.recommendationReason
      : null
function CoverCardText({
  candidate,
  label,
  context,
  shape: { recommendedReference, suggesting, stageReason, currentSourceKinds },
  selected,
  actionableCandidate,
  progress,
  matchId,
  reasonId,
}: {
  candidate: CoverCandidate
  label: string
  context: CoverContext | undefined
  shape: CoverShape
  selected: boolean
  actionableCandidate: boolean
  progress: ChoiceProgress
  /** Ids the card button's description names: the match line and the reason (#1837). */
  matchId: string
  reasonId: string
}) {
  const recommended = candidate.reference === recommendedReference
  // The original a switch replaced is this book's own cover from before, so
  // it carries no match claim and is named for what it is (#1363).
  const retained = candidate.candidateKind === 'retained'
  // A held current cover whose own source names another book reads as the
  // contradiction, keyed on the stage so it stays keepable (#1866).
  const contradicted =
    candidate.contradiction !== undefined ||
    (candidate.incumbent && stageReason === 'current_contradicted')
  const reason = coverCardReason(candidate, context, recommended && !suggesting)
  return (
    <span>
      <small>
        {/* A proposed cover's sources show below, so only the current one is labelled. */}
        {candidate.incumbent ? label : null}
        {recommended
          ? `${candidate.incumbent ? ' · ' : ''}${suggesting ? 'Suggested' : 'Recommended'}`
          : ''}
        {selected && actionableCandidate ? <b className="choice-chosen">Your choice</b> : null}
      </small>
      <strong id={matchId}>
        {retained
          ? coverSourceLabels.retained
          : contradicted
            ? 'Contradicts this book'
            : candidate.matchReason === 'linked_book'
              ? // The owner's own link names the book (#1477).
                'From the linked book'
              : candidate.matchReason === 'linked_title_and_author'
                ? 'Same title and author as the linked book'
                : candidate.matchState === 'same_edition'
                  ? 'Same edition'
                  : candidate.matchState === 'same_book' || candidate.matchReason === 'matched_book'
                    ? 'Same book'
                    : candidate.matchReason === 'title_and_author'
                      ? 'Same title and author'
                      : 'Match not confirmed'}
        {/* The identity stays unverified: only the wording names its basis (#1642). */}
        {candidate.matchReason === 'matched_book' && !contradicted
          ? ' · recording not confirmed'
          : ''}
      </strong>
      {candidate.width === null || candidate.height === null ? null : (
        <span className="cover-dimensions">
          {`${String(candidate.width)} × ${String(candidate.height)} px`}
        </span>
      )}
      {retained ? null : (
        <span className="cover-sources">
          {(candidate.incumbent ? currentSourceKinds : candidate.sourceKinds)
            .map((kind) => coverSourceLabels[kind])
            .join(' · ')}
        </span>
      )}
      {context?.rationale ? <em>{context.rationale}</em> : null}
      {reason === null ? null : (
        <span id={reasonId} className="choice-recommendation">
          {reason}
        </span>
      )}
      {selected && progress?.phase === 'recording' ? <b>Recording…</b> : null}
    </span>
  )
}
function CoverCard({
  candidate,
  index,
  state: { outcome, value, selected, actionableCandidate, disabled: cardDisabled },
  radio,
  label,
  name,
  context,
  shape,
  consideration,
  cover,
  mediaKind,
  progress,
  expandedEvidence,
  revealEvidence,
  choose,
}: {
  candidate: CoverCandidate
  index: number
  state: CoverCardState
  radio: CoverRadio | undefined
  label: string
  /** The card's accessible name. The current cover's card keeps it, readable
   * or not, so it is named for that one action (#1866). */
  name: string
  context: CoverContext | undefined
  shape: CoverShape
  consideration: CoverConsideration
  cover: CoverData
  mediaKind: string
  progress: ChoiceProgress
  choose: ChooseCover
} & EvidenceToggle) {
  const recommended = candidate.reference === shape.recommendedReference
  const source = cover.imageSource(consideration.id, candidate.reference)
  const key = `${candidate.incumbent ? 'current' : 'alternative'}-${String(index)}`
  const evidenceId = `cover-evidence-${String(candidate.ordinal)}-${key}`
  const evidenceKey = `cover:${key}`
  const open = expandedEvidence.has(evidenceKey)
  // The original a switch replaced is this book's own cover from before, so
  // it carries no match claim and is named for what it is (#1363).
  const retained = candidate.candidateKind === 'retained'
  // The name says what pressing does; the description says what the card is
  // and why, which a sighted owner reads on the card itself (#1837).
  const textId = useId()
  const matchId = `${textId}-match`
  const reasonId = `${textId}-reason`
  const describedBy =
    coverCardReason(candidate, context, recommended && !shape.suggesting) === null
      ? matchId
      : `${matchId} ${reasonId}`
  return (
    <article
      className={`cover-choice${open ? ' is-evidence-open' : ''}${candidate.matchState === 'match_not_confirmed' && !retained ? ' is-unconfirmed' : ''}`}
      onFocusCapture={revealFocusedCoverChoice}
    >
      <button
        type="button"
        aria-label={name}
        aria-describedby={describedBy}
        {...radio}
        className={`library-cover-card${candidate.incumbent ? ' is-current' : ''}${recommended ? ' is-recommended' : ''}${selected ? ' is-selected' : ''}`}
        disabled={cardDisabled}
        // One tap records, as on every other field; a card that says Match
        // not confirmed is itself the explicit choice (#1290).
        onClick={() => {
          if (actionableCandidate && !selected) choose(outcome, value)
        }}
      >
        <KeyBadge position={radio?.['aria-posinset']} />
        <span className="library-cover-image">
          {/* A kept original the decoder refused has no preview to ask for (#1362). */}
          {candidate.previewSha256 === null || cover.imageFailed(candidate.reference) ? (
            <span role="img" aria-label="Cover unavailable">
              Image unavailable
            </span>
          ) : (
            <img
              src={source}
              alt={label}
              loading="lazy"
              decoding="async"
              onError={() => cover.markImageFailed(candidate.reference)}
            />
          )}
        </span>
        <CoverCardText
          candidate={candidate}
          label={label}
          context={context}
          shape={shape}
          selected={selected}
          actionableCandidate={actionableCandidate}
          progress={progress}
          matchId={matchId}
          reasonId={reasonId}
        />
      </button>
      <CoverObservationEvidence
        label={label}
        mediaKind={mediaKind}
        observation={candidate.observation}
        reviewCompleted={consideration.reviewerStatus === 'completed'}
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
        <ChoiceEvidencePanel context={context} evidenceId={evidenceId} label={label} />
      ) : null}
    </article>
  )
}
// The current cover Audiobookshelf did not return, offered to keep (#1865).
function UnavailableCurrentCard({
  radio,
  field,
  keeps,
  keepSelected,
  disabled,
  currentContext,
  progress,
  expandedEvidence,
  revealEvidence,
  choose,
}: {
  radio: CoverRadio | undefined
  field: ReviewField
  keeps: boolean
  keepSelected: boolean
  disabled: boolean
  currentContext: CoverContext | undefined
  progress: ChoiceProgress
  choose: ChooseCover
} & EvidenceToggle) {
  const textId = useId()
  const reason =
    currentContext?.recommendation === 'recommended' && currentContext.recommendationReason
      ? currentContext.recommendationReason
      : null
  return (
    <article className="cover-choice" onFocusCapture={revealFocusedCoverChoice}>
      <button
        type="button"
        aria-label="Keep current cover"
        aria-describedby={reason === null ? `${textId}-match` : `${textId}-match ${textId}-reason`}
        {...radio}
        className={`library-cover-card is-current${keepSelected ? ' is-selected' : ''}`}
        disabled={disabled || !keeps}
        onClick={() => {
          if (keeps) choose('keep_current')
        }}
      >
        <KeyBadge position={radio?.['aria-posinset']} />
        <span className="library-cover-image" role="img" aria-label="Current cover unavailable">
          Image unavailable
        </span>
        <span>
          <small>
            Current cover
            {keepSelected ? <b className="choice-chosen">Your choice</b> : null}
          </small>
          <strong id={`${textId}-match`}>Unavailable</strong>
          {currentContext?.rationale ? <em>{currentContext.rationale}</em> : null}
          {reason === null ? null : (
            <span id={`${textId}-reason`} className="choice-recommendation">
              {reason}
            </span>
          )}
          {commandSelected(progress?.command ?? null, field.destination, 'keep_current') &&
          progress?.phase === 'recording' ? (
            <b>Recording…</b>
          ) : null}
        </span>
      </button>
      {currentContext && (currentContext.evidence.length || currentContext.limitations.length) ? (
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
  )
}
// What the comparison is ready to do, its fault and, where it says more than
// the cards, why.
function CoverComparisonHeader({
  consideration,
  field,
  shape: { recommendedReference, suggesting, faultLabel, stage, stageReason },
}: {
  consideration: CoverConsideration
  field: ReviewField
  shape: CoverShape
}) {
  return (
    <header>
      <strong>
        {/* Once the owner has answered, a suggestion is no longer pending, so
            the field is headed as any other answered comparison (#1866). */}
        {recommendedReference !== null && !(suggesting && field.humanDecision)
          ? suggesting
            ? 'Switch suggested'
            : 'Recommended cover ready'
          : consideration.ready
            ? 'Cover choices ready'
            : consideration.providerFetching
              ? 'Still searching for covers'
              : 'No cover recommended'}
      </strong>
      {faultLabel !== null ? <StatusPill tone="notice">{faultLabel}</StatusPill> : null}
      {/* The recommended card carries its own reason, so its header has no
          caption (#1765); a suggestion's fault pill is its only reason. A
          kept cover with a fault shows the pill alone, since "no fault" would
          contradict it (#1363), and so does a current cover whose own source
          names another book (#1866). Once the owner has chosen, the stage
          says nothing beside their choice (#1362). */}
      {recommendedReference !== null ||
      (!consideration.providerFetching && field.humanDecision) ||
      (!consideration.providerFetching &&
        stage?.outcome === 'hold' &&
        stageReason === 'current_contradicted') ||
      (consideration.ready &&
        !consideration.providerFetching &&
        stage?.outcome === 'keep' &&
        stageReason !== null) ? null : (
        <small>
          {consideration.providerFetching
            ? 'Covers found so far are shown. You can choose when the search finishes.'
            : stage?.outcome === 'hold'
              ? 'The title names a different book from most covers. Check the title first.'
              : stage?.outcome === 'keep' &&
                  stageReason === null &&
                  consideration.incumbent.state === 'unavailable'
                ? 'Audiobookshelf did not return the current cover, so Mechanical Librarian keeps it until the next scan.'
                : consideration.ready && stage?.outcome === 'keep'
                  ? 'The current cover has no fault, so it is kept.'
                  : 'Only confirmed matches can be recommended.'}
        </small>
      )}
    </header>
  )
}
// Who reviewed the covers, and whose choice stands.
function CoverReviewStatus({
  consideration,
  field,
}: {
  consideration: CoverConsideration
  field: ReviewField
}) {
  const reviewerLabel =
    consideration.reviewerStatus === 'completed'
      ? 'Visual review complete'
      : consideration.reviewerStatus === 'failed' || consideration.reviewerStatus === 'unavailable'
        ? 'Visual review is unavailable.'
        : 'Manual cover comparison'
  // A review that failed at chat time says what to change (#1330-#1332).
  const reviewerDetail = consideration.reviewerFailureCode
    ? reviewerFailureCopy[consideration.reviewerFailureCode]
    : reviewerUnavailableDetail(consideration.reviewerUnavailableReason)
  return (
    <>
      <p className="reviewer-unavailable-detail">{reviewerLabel}</p>
      {reviewerDetail ? (
        <p className="reviewer-unavailable-detail">
          <SettingsPathText text={reviewerDetail} />
        </p>
      ) : null}
      {consideration.reviewerModel ? (
        <p>Reviewer: {coverReviewerLabel(consideration.reviewerModel)}</p>
      ) : null}
      {field.humanDecision ? (
        <p className="reviewer-unavailable-detail">
          {field.editable
            ? 'You can change your choice until it is written.'
            : 'Your choice is being written and cannot be changed yet.'}
        </p>
      ) : field.automaticDecision ? (
        <p className="reviewer-unavailable-detail">
          Chosen automatically. It changes again only for a fault, or when you choose.
        </p>
      ) : null}
    </>
  )
}
// A merged duplicate is the same image as a card shown, not an unavailable
// candidate, so it is counted apart (#1290). An admission truncation stands
// for covers that were never fetched, not for one image, so it is not a
// cover not shown either (#1778).
function CoverOmissions({
  consideration,
  merged,
}: {
  consideration: CoverConsideration
  merged: number
}) {
  const unavailable = consideration.omissions.filter(
    (omission) => omission.reason !== 'duplicate_preview' && omission.reason !== 'source_truncated',
  )
  return (
    <>
      {merged ? (
        <p className="cover-merged">
          {String(merged)} duplicate image{merged === 1 ? '' : 's'} merged. Each card names every
          source that offered it.
        </p>
      ) : null}
      {unavailable.length ? (
        <details className="library-cover-excluded">
          <summary>
            {String(unavailable.length)} cover{unavailable.length === 1 ? '' : 's'} not shown{' '}
            <small>Show why</small>
          </summary>
          {unavailable.map((omission, index) => (
            <p key={String(index)}>
              {typeof omission.reason === 'string'
                ? coverNoteLabel(omission.reason)
                : 'No reason was recorded.'}
            </p>
          ))}
        </details>
      ) : null}
    </>
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
  prepareFailure,
  question = decisionQuestion('cover'),
  currentLabel = 'Current cover',
  keepLabel = 'Keep current cover',
  coversLink = true,
  calibration = false,
}: {
  reviewCaseId: string
  mediaKind: string
  field: ReviewField
  disabled: boolean
  progress: ChoiceProgress
  choose: ChooseCover
  prepareFailure: { failure: CoverPrepareFailure | null; record: RecordCoverPrepareFailure }
} & EvidenceToggle &
  CoverChoiceWording) {
  const canRetryReviewer = useCanRetryReviewer()
  const cover = useCoverChoiceData(reviewCaseId, prepareFailure.record)
  const failure = prepareFailure.failure
  const consideration = field.coverConsideration
  const keeps = field.decisionActions.includes('keep_current')
  const accepts = field.decisionActions.includes('accept')
  const trackSection = useSuggestedCardInView(
    calibration || !field.coverTasteSuggestion
      ? null
      : `${reviewCaseId} ${field.coverTasteSuggestion.verdict_id}`,
  )
  if (consideration?.current !== true)
    return <PrepareCoverComparison disabled={disabled} cover={cover} failure={failure} />
  const prepared = coverComparisonShape(
    consideration,
    mediaKind,
    field.coverTasteSuggestion ?? null,
  )
  const shape = calibration ? calibrationShape(prepared, reviewCaseId) : prepared
  const { candidates, expanded, incumbent, alternatives } = shape

  const coverContexts = consideration.choiceContexts
  const currentContext = coverContexts[0]
  const cardState = coverCardState({
    field,
    progress,
    keeps,
    accepts,
    locked: disabled || consideration.providerFetching,
    imageFailed: cover.imageFailed,
  })
  const unavailableCurrent = consideration.incumbent.state === 'unavailable' && !incumbent
  const keepSelected = choiceSelected(field, progress, 'keep_current')
  const coverRadios = coverRadioMap(shape, cardState, {
    shown: unavailableCurrent,
    checked: keepSelected,
    disabled: disabled || !keeps,
  })
  const names = coverNames(candidates)
  const card = (candidate: CoverCandidate, index: number) => (
    <CoverCard
      key={candidate.reference}
      candidate={candidate}
      index={index}
      state={cardState(candidate)}
      radio={coverRadios.get(candidate.reference)}
      label={
        candidate.incumbent ? currentLabel : (names.get(candidate.reference) ?? 'Proposed cover')
      }
      name={
        candidate.incumbent
          ? keepLabel
          : `Choose ${names.get(candidate.reference) ?? 'Proposed cover'}`
      }
      context={
        candidate.incumbent
          ? currentContext
          : coverContexts.find((item) => item.alternativeId === candidate.reference)
      }
      shape={shape}
      consideration={consideration}
      cover={cover}
      mediaKind={mediaKind}
      progress={progress}
      expandedEvidence={expandedEvidence}
      revealEvidence={revealEvidence}
      choose={choose}
    />
  )
  const reviewerUnavailable =
    consideration.reviewerStatus === 'failed' || consideration.reviewerStatus === 'unavailable'
  return (
    <section
      ref={trackSection}
      className={`library-cover-comparison${expanded ? ' is-expanded' : ''}`}
      aria-label="Choose a cover"
    >
      {calibration ? null : (
        <>
          <CoverComparisonHeader consideration={consideration} field={field} shape={shape} />
          <CoverReviewStatus consideration={consideration} field={field} />
        </>
      )}
      {coversLink && !calibration ? (
        <Link
          className="covers-link"
          to="/library"
          search={{ lens: 'all', view: 'covers', tab: 'all', cover: reviewCaseId }}
        >
          Covers
        </Link>
      ) : null}
      {reviewerUnavailable && canRetryReviewer && !calibration ? (
        <Button
          className="secondary-action"
          type="button"
          disabled={disabled || cover.prepare.isPending}
          onClick={() => cover.prepare.mutate('retry')}
        >
          Retry visual review
        </Button>
      ) : null}
      {failure ? <p role="alert">{coverPrepareFailureCopy(failure)}</p> : null}
      {expanded && incumbent ? (
        <div
          className="library-cover-pinned"
          role="radiogroup"
          aria-label={question}
          onKeyDown={moveChoiceFocus}
        >
          <aside>{card(incumbent, candidates.indexOf(incumbent))}</aside>
          <div className="library-cover-gallery">
            <header>
              <strong>Other covers</strong>
              {/* A count of covers: some may be ones you cannot choose (#1790). */}
              <small>{`${String(alternatives.length)} covers`}</small>
            </header>
            <div>
              {alternatives.map((candidate) => card(candidate, candidates.indexOf(candidate)))}
            </div>
          </div>
        </div>
      ) : (
        <div
          className="library-cover-layout"
          role="radiogroup"
          aria-label={question}
          onKeyDown={moveChoiceFocus}
        >
          {unavailableCurrent ? (
            <UnavailableCurrentCard
              radio={coverRadios.get('current-unavailable')}
              field={field}
              keeps={keeps}
              keepSelected={keepSelected}
              disabled={disabled}
              currentContext={currentContext}
              progress={progress}
              expandedEvidence={expandedEvidence}
              revealEvidence={revealEvidence}
              choose={choose}
            />
          ) : null}
          {candidates.map(card)}
        </div>
      )}
      {/* Below the cards, never above them, so the choices stay where they
          were (owner, 2026-11-16). */}
      {shape.taste === null ? null : <SuggestedBecause suggestion={shape.taste} />}
      <CoverOmissions consideration={consideration} merged={shape.duplicates.length} />
    </section>
  )
}
// On a narrow screen the cards scroll sideways, so a work that opens with a
// suggestion scrolls its card row to show the Suggested card, once per work
// and suggestion: never the page, never down, and never again after the
// owner has scrolled the row (#1372 PR 4b).
function useSuggestedCardInView(key: string | null) {
  const section = useRef<HTMLElement>(null)
  const done = useRef<string | null>(null)
  useLayoutEffect(() => {
    const root = section.current
    if (key === null || done.current === key || !root) return
    done.current = key
    if (!root.querySelector('.cover-taste-why')) return
    const card = root.querySelector<HTMLElement>('.library-cover-card.is-recommended')
    const track = card?.closest<HTMLElement>('.library-cover-layout, .library-cover-pinned')
    if (!card || !track || track.scrollWidth <= track.clientWidth) return
    const shown = card.closest<HTMLElement>('.cover-choice') ?? card
    const start = shown.getBoundingClientRect().left - track.getBoundingClientRect().left
    const end = start + shown.getBoundingClientRect().width
    if (start >= 0 && end <= track.clientWidth) return
    track.scrollLeft = Math.max(0, track.scrollLeft + start)
  })
  return section
}

// Why the taste judge suggests the marked card: its strength and the rules
// it cited, each the stored rule shown as text (#1372 PR 4b).
function SuggestedBecause({ suggestion }: { suggestion: CoverTasteSuggestion }) {
  const navigate = useNavigate()
  const id = useId()
  // The cited rules under their Taste profile sections, in its order, each
  // keeping the order the suggestion model cited it in.
  const groups = (Object.keys(tasteSectionHeadings) as TasteSection[])
    .map((section) => ({
      section,
      rules: suggestion.rules.filter((rule) => rule.section === section),
    }))
    .filter((group) => group.rules.length > 0)
  return (
    <aside className="cover-taste-why" aria-labelledby={`${id}-why`}>
      <header>
        <strong id={`${id}-why`}>Suggested because</strong>
        <StatusPill tone={suggestion.strength === 'strong' ? 'accent' : 'neutral'}>
          {suggestion.strength === 'strong' ? 'Strong' : 'Likely'}
        </StatusPill>
        {/* Rules from an earlier version can read differently now (Q4). */}
        {suggestion.version === suggestion.version_in_use ? null : (
          <StatusPill tone="neutral">{`From version ${String(suggestion.version)}`}</StatusPill>
        )}
      </header>
      {groups.map((group) => (
        <div key={group.section} className="cover-taste-group">
          <span id={`${id}-${group.section}`} className="cover-taste-section">
            {tasteSectionHeadings[group.section]}
          </span>
          <ol aria-labelledby={`${id}-${group.section}`}>
            {group.rules.map((rule) => (
              <li key={rule.number}>
                <span className="cover-taste-rule">{String(rule.number)}</span>
                <span>{rule.text}</span>
              </li>
            ))}
          </ol>
        </div>
      ))}
      <button
        type="button"
        className="cover-taste-edit"
        onClick={() => {
          void navigate({
            to: '/library',
            search: { lens: 'all', view: 'covers', tab: 'taste' },
          }).then(() => {
            window.requestAnimationFrame(() => {
              document.querySelector<HTMLElement>('.covers-tabs [aria-pressed="true"]')?.focus()
            })
          })
        }}
      >
        Edit taste profile
      </button>
    </aside>
  )
}
