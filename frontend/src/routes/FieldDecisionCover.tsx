import { Link } from '@tanstack/react-router'
import { queryWorkClass, useRequiredQuery as useQuery } from '../queryWork'
import type { SyntheticEvent } from 'react'

import { refusalCopy, uncertainWriteOutcome } from '../api/http'
import type { CoverObservationView, DecisionCommand } from '../api/http'
import { choiceRadios, moveChoiceFocus } from '../choiceGroup'
import { useCoverChoiceData } from '../coverChoices'
import type { CoverPrepareFailure, RecordCoverPrepareFailure } from '../coverChoices'
import {
  coverNoteLabel,
  coverReviewerLabel,
  coverSourceLabels,
  reviewerFailureCopy,
} from '../labels'
import { useReviewContext } from '../router'
import { Button } from '../components/Button'
import { StatusPill } from '../components/StatusPill'
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
  coversLink?: boolean
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
  coversLink = true,
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
  prepareFailure: { failure: CoverPrepareFailure | null; record: RecordCoverPrepareFailure }
} & CoverChoiceWording) {
  const { http } = useReviewContext()
  const access = useQuery({
    queryKey: ['reviewer-access'] as const,
    queryFn: (context) => http.getReviewerAccess(context.signal, queryWorkClass(context)),
    retry: false,
  })
  const mapping = access.isSuccess ? access.data.mapping : null
  const currentReviewer = mapping && access.isSuccess ? access.data[mapping.engine] : null
  const canRetryReviewer =
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
  const cover = useCoverChoiceData(reviewCaseId, prepareFailure.record)
  const failure = prepareFailure.failure
  const consideration = field.coverConsideration
  const keeps = field.decisionActions.includes('keep_current')
  const accepts = field.decisionActions.includes('accept')
  if (consideration?.current !== true)
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
  const candidates = consideration.candidates
  const expanded = candidates.length > 3
  const incumbent = candidates.find((candidate) => candidate.incumbent)
  const alternatives = candidates.filter((candidate) => !candidate.incumbent)
  const coverContexts = consideration.choiceContexts
  const currentContext = coverContexts[0]
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
  // A contradiction says why the cover cannot be chosen, ahead of the matched
  // book (#1790). Every other card the line reaches says Match not confirmed in
  // its title, so no line repeats it (#1765).
  const excludedReason = (candidate: (typeof candidates)[number]) =>
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
  const cardState = (candidate: (typeof candidates)[number]) => {
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
      disabled:
        disabled ||
        consideration.providerFetching ||
        !actionableCandidate ||
        cover.imageFailed(candidate.reference),
    }
  }
  const unavailableCurrent = consideration.incumbent.state === 'unavailable' && !incumbent
  const keepSelected = choiceSelected(field, progress, 'keep_current')
  // The cards in reading order, one radio group whose Tab stop and positions
  // follow the direct choices' rules (#1290).
  const coverRadios = new Map(
    choiceRadios(
      [
        ...(unavailableCurrent ? [null] : []),
        ...(expanded && incumbent ? [incumbent, ...alternatives] : candidates),
      ],
      (candidate) => {
        if (candidate === null) return { checked: keepSelected, disabled: disabled || !keeps }
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
  const names = coverNames(candidates)
  const card = (candidate: (typeof candidates)[number], index: number) => {
    const source = cover.imageSource(consideration.id, candidate.reference)
    const {
      outcome,
      value,
      selected,
      actionableCandidate,
      disabled: cardDisabled,
    } = cardState(candidate)
    const eligibleCandidate = candidate.incumbent || candidate.manualEligible
    const recommended = candidate.reference === recommendedReference
    const context = candidate.incumbent
      ? currentContext
      : coverContexts.find((item) => item.alternativeId === candidate.reference)
    const key = `${candidate.incumbent ? 'current' : 'alternative'}-${String(index)}`
    const evidenceId = `cover-evidence-${String(candidate.ordinal)}-${key}`
    const label = candidate.incumbent
      ? currentLabel
      : (names.get(candidate.reference) ?? 'Proposed cover')
    const evidenceKey = `cover:${key}`
    const open = expandedEvidence.has(evidenceKey)
    // The original a switch replaced is this book's own cover from before, so
    // it carries no match claim and is named for what it is (#1363).
    const retained = candidate.candidateKind === 'retained'
    return (
      <article
        className={`cover-choice${open ? ' is-evidence-open' : ''}${candidate.matchState === 'match_not_confirmed' && !retained ? ' is-unconfirmed' : ''}`}
        key={candidate.reference}
        onFocusCapture={revealFocusedCoverChoice}
      >
        <button
          type="button"
          aria-label={`Choose ${label}`}
          {...coverRadios.get(candidate.reference)}
          className={`library-cover-card${candidate.incumbent ? ' is-current' : ''}${recommended ? ' is-recommended' : ''}${selected ? ' is-selected' : ''}`}
          disabled={cardDisabled}
          // One tap records, as on every other field; a card that says Match
          // not confirmed is itself the explicit choice (#1290).
          onClick={() => {
            if (actionableCandidate && !selected) choose(outcome, value)
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
                loading="lazy"
                decoding="async"
                onError={() => cover.markImageFailed(candidate.reference)}
              />
            )}
          </span>
          <span>
            <small>
              {/* A proposed cover's sources show below, so only the current one is labelled. */}
              {candidate.incumbent ? label : null}
              {recommended ? `${candidate.incumbent ? ' · ' : ''}Recommended` : ''}
              {selected && actionableCandidate ? (
                <b className="choice-chosen">Your choice</b>
              ) : null}
            </small>
            <strong>
              {retained
                ? coverSourceLabels.retained
                : candidate.contradiction
                  ? 'Contradicts this book'
                  : candidate.matchState === 'same_edition'
                    ? 'Same edition'
                    : candidate.matchState === 'same_book' ||
                        candidate.matchReason === 'matched_book'
                      ? 'Same book'
                      : candidate.matchReason === 'title_and_author'
                        ? 'Same title and author'
                        : 'Match not confirmed'}
              {/* The identity stays unverified: only the wording names its basis (#1642). */}
              {candidate.matchReason === 'matched_book' && !candidate.contradiction
                ? ' · recording not confirmed'
                : ''}
            </strong>
            <span className="cover-dimensions">
              {`${String(candidate.width)} × ${String(candidate.height)} px`}
            </span>
            {retained ? null : (
              <span className="cover-sources">
                {candidate.sourceKinds.map((kind) => coverSourceLabels[kind]).join(' · ')}
              </span>
            )}
            {context?.rationale ? <em>{context.rationale}</em> : null}
            {!eligibleCandidate ||
            candidate.matchState === 'match_not_confirmed' ||
            candidate.matchReason === 'matched_book' ? (
              excludedReason(candidate) === null ? null : (
                <span className="choice-recommendation">{excludedReason(candidate)}</span>
              )
            ) : recommended && context?.recommendationReason ? (
              <span className="choice-recommendation">{context.recommendationReason}</span>
            ) : null}
            {selected && progress?.phase === 'recording' ? <b>Recording…</b> : null}
          </span>
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
  const reviewerUnavailable =
    consideration.reviewerStatus === 'failed' || consideration.reviewerStatus === 'unavailable'
  const reviewerLabel =
    consideration.reviewerStatus === 'completed'
      ? 'Visual review complete'
      : reviewerUnavailable
        ? 'Visual review is unavailable.'
        : 'Manual cover comparison'
  // A review that failed at chat time says what to change (#1330-#1332).
  const reviewerDetail = consideration.reviewerFailureCode
    ? reviewerFailureCopy[consideration.reviewerFailureCode]
    : reviewerUnavailableDetail(consideration.reviewerUnavailableReason)
  // A merged duplicate is the same image as a card shown, not an unavailable
  // candidate, so it is counted apart (#1290). An admission truncation stands
  // for covers that were never fetched, not for one image, so it is not a
  // cover not shown either (#1778).
  const merged = consideration.omissions.filter(
    (omission) => omission.reason === 'duplicate_preview',
  ).length
  const unavailable = consideration.omissions.filter(
    (omission) => omission.reason !== 'duplicate_preview' && omission.reason !== 'source_truncated',
  )
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
                ? 'Still searching for covers'
                : 'No cover recommended'}
        </strong>
        {/* The recommended card carries its own reason, so its header has no caption (#1765). */}
        {recommendedReference !== null ? null : (
          <small>
            {consideration.ready && policyReasons.includes('actual_target_unverified')
              ? 'The cover in Audiobookshelf could not be confirmed, so it is not replaced automatically.'
              : consideration.ready && policyReasons.includes('confirmed_incumbent_not_improved')
                ? 'No confirmed cover is better than the current one, so it is not replaced automatically.'
                : consideration.providerFetching
                  ? 'Covers found so far are shown. You can choose when the search finishes.'
                  : 'Only confirmed matches can be recommended.'}
          </small>
        )}
      </header>
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
          Chosen automatically. Only a better confirmed cover, or your choice, replaces it.
        </p>
      ) : null}
      {coversLink ? (
        <Link
          className="covers-link"
          to="/library"
          search={{ lens: 'all', view: 'covers', tab: 'all', cover: reviewCaseId }}
        >
          Covers
        </Link>
      ) : null}
      <p className="cover-track-hint">
        Every cover is shown whole in one row. Scroll the row, or move between covers with the arrow
        keys, to compare them.
      </p>
      {reviewerUnavailable && canRetryReviewer ? (
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
            <article className="cover-choice" onFocusCapture={revealFocusedCoverChoice}>
              <button
                type="button"
                aria-label="Keep current cover"
                {...coverRadios.get('current-unavailable')}
                className={`library-cover-card is-current${keepSelected ? ' is-selected' : ''}`}
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
                  <small>
                    Current cover
                    {keepSelected ? <b className="choice-chosen">Your choice</b> : null}
                  </small>
                  <strong>Unavailable</strong>
                  {currentContext?.rationale ? <em>{currentContext.rationale}</em> : null}
                  {currentContext?.recommendation === 'recommended' &&
                  currentContext.recommendationReason ? (
                    <span className="choice-recommendation">
                      {currentContext.recommendationReason}
                    </span>
                  ) : null}
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
    </section>
  )
}
