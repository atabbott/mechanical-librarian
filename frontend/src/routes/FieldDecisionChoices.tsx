import { queryWorkClass } from '../queryWork'
import { KeyBadge } from './ReviewKeys'
import { descriptionAdviceStatus } from '../descriptionAdviceStatus'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useId, useRef, useState } from 'react'

import { BuildMismatchError } from '../api/http'
import type { ChoiceContextView, EvidenceView } from '../api/http'
import { StaleBuildRead } from '../components/StaleBuildRead'
import type { choiceRadios } from '../choiceGroup'
import { focusAfterRetry, retryInPlace } from '../focusAfterRetry'
import {
  evidenceRoleLabel,
  fieldObservationText,
  observationText,
  materialReasonLabel,
} from '../labels'
import { useReviewContext } from '../router'

import type { ReviewField } from '../fieldDecision'
import type { PinControl } from '../fieldPin'
import { PinButton } from './FieldDecisionPin'
// A limitation is a comparison condition, which reads as a sentence, or the
// server's own sentence; an unnamed condition code is never shown.
const limitationText = (limitation: string) => {
  const label = materialReasonLabel(limitation)
  if (label !== undefined) return `${label}.`
  return /^[a-z0-9_]+$/u.test(limitation) ? 'Another comparison condition applies.' : limitation
}
// A source record offered for the book's title and author, not a confirmed
// match, says so beside its name (#1355). The library's own value never does.
const sourceBasis = (
  context: ChoiceContextView,
  role: ChoiceContextView['evidence'][number]['role'],
) =>
  context.matchBasis === 'title_and_author' && role !== 'local_presence'
    ? ' · same title and author'
    : ''
export function ChoiceEvidencePanel({
  context,
  evidenceId,
  label,
  name,
}: {
  context: ChoiceContextView | undefined
  evidenceId: string
  label: string
  /** The region's name when it should match the Evidence control that opens it. */
  name?: string
}) {
  // A record offered for the book's title and author offers its value; it
  // confirms nothing, so it counts toward no "sources confirm" line (#1355).
  const offered = context?.matchBasis === 'title_and_author'
  const corroboratingSources = offered
    ? 0
    : new Set(
        context?.evidence
          .filter((row) => row.role === 'external_corroboration')
          .map((row) => row.source),
      ).size
  return (
    <section className="choice-evidence" id={evidenceId} aria-label={name ?? `${label} evidence`}>
      {corroboratingSources ? (
        <p>
          {corroboratingSources}{' '}
          {corroboratingSources === 1 ? 'source confirms' : 'sources confirm'} this choice.
        </p>
      ) : null}
      {context?.evidence.length ? (
        <ul>
          {context.evidence.map((row, index) => (
            <li key={`${row.source}:${row.observation}:${String(index)}`}>
              <strong>
                {row.source}
                {sourceBasis(context, row.role)}
              </strong>
              <span>
                {offered && row.role === 'external_corroboration'
                  ? 'Offers this value'
                  : evidenceRoleLabel(row.role)}
              </span>
              <p>{observationText(row)}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p>No source is recorded for this value.</p>
      )}
      {context?.limitations.length ? (
        <aside>
          <strong>Limitation</strong>
          {context.limitations.map((limitation) => (
            <p key={limitation}>{limitationText(limitation)}</p>
          ))}
        </aside>
      ) : null}
    </section>
  )
}
export function DescriptionText({ value }: { value: string }) {
  const [expanded, setExpanded] = useState(false)
  const long = value.length > 280
  return (
    <div className="description-value">
      <p>{long && !expanded ? `${value.slice(0, 240)}…` : value}</p>
      {long ? (
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show less description' : 'Read full description'}
        </button>
      ) : null}
    </div>
  )
}
export function RecordedFieldObservations({
  reviewCaseId,
  field,
}: {
  reviewCaseId: string
  field: ReviewField
}) {
  const { http } = useReviewContext()
  const evidence = useInfiniteQuery({
    queryKey: ['review-case', reviewCaseId, 'evidence'],
    initialPageParam: undefined as string | undefined,
    queryFn: (context) =>
      http.getEvidence(reviewCaseId, context.pageParam, context.signal, queryWorkClass(context)),
    getNextPageParam: (page: EvidenceView) => page.nextCursor,
    retry: false,
    staleTime: 30_000,
  })
  const choiceEvidence = field.choiceContexts.flatMap((context) => context.evidence)
  const observations = (evidence.data?.pages.flatMap((page) => page.items) ?? []).filter(
    (item) =>
      item.destinations.includes(field.destination) &&
      !choiceEvidence.some(
        (linked) =>
          linked.source === item.source &&
          linked.role === item.role &&
          linked.observation === item.observation,
      ),
  )
  const missing = evidence.data?.pages[0]?.missingLineageCount ?? 0
  // Try again, or Reload once the tab is on an earlier build.
  const observationsRetry = useRef<HTMLButtonElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const list = useRef<HTMLUListElement>(null)
  // A first-page retry has no data, so the read goes back to pending: the
  // section stays while it reads, and its heading holds focus.
  const [retried, setRetried] = useState(false)
  const retryReading = retried && evidence.isPending
  // The section is there only when this field has observations to show, more
  // may be loaded, they could not be read (#1519), or a retry is reading them.
  if (
    !observations.length &&
    !evidence.isError &&
    !evidence.hasNextPage &&
    !missing &&
    !retryReading
  )
    return null
  return (
    <section className="recorded-field-observations" aria-label="Other source values">
      <h3 ref={heading} tabIndex={-1}>
        Other source values
      </h3>
      {observations.length ? (
        <ul className="evidence-values" ref={list}>
          {observations.map((item, index) => (
            <li key={`${item.source}:${item.observation}:${String(index)}`} tabIndex={-1}>
              <p>{fieldObservationText(item, field.destination)}</p>
              <small>
                {item.source} · {evidenceRoleLabel(item.role)}
              </small>
            </li>
          ))}
        </ul>
      ) : null}
      {missing ? <p>Some source values could not be shown.</p> : null}
      {retryReading ? <p role="status">Loading source values…</p> : null}
      {evidence.error instanceof BuildMismatchError ? (
        <StaleBuildRead lead="Source values were not loaded." ref={observationsRetry} />
      ) : evidence.isError ? (
        <p role="alert">
          Source values could not be loaded.{' '}
          <button
            ref={observationsRetry}
            type="button"
            onClick={() => {
              const nextPage = evidence.isFetchNextPageError
              setRetried(true)
              // Either read removes this button while it reads.
              retryInPlace(
                () => heading.current,
                () => (nextPage ? evidence.fetchNextPage() : evidence.refetch()),
                (result) =>
                  result.isSuccess
                    ? // A read that leaves this field nothing to show removes the
                      // section (#1519): the field's question takes focus instead.
                      (heading.current ??
                      document.querySelector<HTMLElement>('.decision-canvas .decision-question'))
                    : observationsRetry.current,
              )
            }}
          >
            Try again
          </button>
        </p>
      ) : null}
      {evidence.hasNextPage ? (
        <p>
          {/* Busy, not disabled, so focus stays on it while it reads. */}
          <button
            type="button"
            aria-disabled={evidence.isFetchingNextPage || undefined}
            onClick={() => {
              if (evidence.isFetchingNextPage) return
              const from = observations.length
              // A last page removes this button: focus moves to the first
              // observation it added.
              void evidence
                .fetchNextPage()
                .then(() =>
                  focusAfterRetry(
                    () => list.current?.querySelectorAll('li')[from] ?? heading.current,
                  ),
                )
            }}
          >
            {evidence.isFetchingNextPage ? 'Loading more…' : 'Show more source values'}
          </button>
        </p>
      ) : null}
    </section>
  )
}
export function MetadataChoice({
  advice,
  automatic = false,
  beforeChange = false,
  retainedAdvice = false,
  context,
  current,
  disabled,
  evidenceName,
  expanded,
  label,
  longDescription,
  onChoose,
  pin,
  pinned = false,
  radio,
  recording,
  selected,
  toggle,
  value,
}: {
  advice?: ReviewField['descriptionAdvice']
  // The selected badge names Automatic when the pick is Automatic's (#1765).
  automatic?: boolean
  // The value the case read before its own write, never called current (#1523).
  beforeChange?: boolean
  retainedAdvice?: boolean
  context: ChoiceContextView | undefined
  current: boolean
  disabled: boolean
  // The Evidence control's name when the card has no visible label (#1519).
  evidenceName?: string
  expanded: boolean
  label: string
  longDescription: boolean
  onChoose: () => void
  // The pin at the card's foot, where the field offers one (#1346).
  pin?: PinControl | undefined
  // The card carries the field's pin: it reads as chosen and says Pinned.
  pinned?: boolean
  radio: ReturnType<typeof choiceRadios>[number]['radio']
  recording: boolean
  selected: boolean
  toggle: () => void
  value: string
}) {
  const adviceSource =
    retainedAdvice && advice?.selected_advice ? advice.selected_advice.candidate : advice
  const evidenceId = `choice-evidence-${current ? 'current' : (context?.alternativeId ?? 'alternative')}`
  const evidenceLabel =
    beforeChange && longDescription
      ? 'Evidence for the description before your change'
      : (evidenceName ??
        (longDescription ? `Evidence for ${label} description` : `Evidence for ${label}: ${value}`))
  // The card's source line says a title-and-author value as Evidence does.
  const rationale = adviceSource
    ? `${adviceSource.engine} · ${adviceSource.model}`
    : context?.rationale && !current && context.matchBasis === 'title_and_author'
      ? `${context.rationale} · same title and author`
      : context?.rationale
  const recommendation = recording
    ? 'Recording…'
    : advice
      ? descriptionAdviceStatus(advice, retainedAdvice)
      : context?.recommendation === 'recommended'
        ? context.recommendationReason
        : ''
  const hasEvidence = Boolean(context && (context.evidence.length || context.limitations.length))
  const valueId = useId()
  const kind = advice ? 'advised' : current ? 'current' : 'proposed'
  const choiceName = beforeChange
    ? `Choose the ${longDescription ? 'description before your change' : `value before your change: ${value}`}`
    : `Choose ${kind} ${longDescription ? 'description' : `value: ${value}`}`
  const eyebrow = `${label}${context?.recommendation === 'recommended' ? `${label ? ' · ' : ''}Recommended` : ''}`
  // A pin carried from an earlier case marks a card no decision of this case chose.
  const badge = selected
    ? `${automatic ? 'Automatic’s choice' : 'Your choice'}${pinned ? ' · Pinned' : ''}`
    : pinned
      ? 'Pinned'
      : null
  return (
    <article
      className={`metadata-choice${current ? ' is-current' : ''}${context?.recommendation === 'recommended' ? ' is-recommended' : ''}${selected || pinned ? ' is-selected' : ''}${expanded ? ' is-evidence-open' : ''}`}
    >
      {longDescription ? (
        <div className="description-choice-content">
          {label ? <small>{label}</small> : null}
          <DescriptionText key={value} value={value} />
        </div>
      ) : null}
      <button
        type="button"
        className="value-choice choice-select"
        aria-label={choiceName}
        {...radio}
        disabled={disabled}
        // Choosing the pinned value again would record a new decision and end the pin.
        onClick={pinned ? undefined : onChoose}
      >
        <KeyBadge position={radio['aria-posinset']} />
        <small>
          {eyebrow ? <span className="choice-eyebrow">{eyebrow}</span> : null}
          {badge ? <b className="choice-chosen">{badge}</b> : null}
        </small>
        <strong id={valueId}>
          {/* Keeps the first line clear of the key badge (styles.css). */}
          <i className="review-key-space" />
          <wbr className="review-key-break" />
          {longDescription
            ? beforeChange
              ? 'Choose the description before your change'
              : `Choose ${kind} description`
            : value}
        </strong>
        {rationale ? <span>{rationale}</span> : null}
        {recommendation ? <span className="choice-recommendation">{recommendation}</span> : null}
      </button>
      {hasEvidence || pin ? (
        <div className="choice-foot">
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
          {pin ? <PinButton {...pin} describedBy={valueId} /> : null}
        </div>
      ) : null}
      {expanded ? (
        <ChoiceEvidencePanel
          context={context}
          evidenceId={evidenceId}
          label={label}
          name={evidenceLabel}
        />
      ) : null}
    </article>
  )
}
