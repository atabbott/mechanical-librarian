import { queryWorkClass } from '../queryWork'
import { descriptionAdviceStatus } from '../descriptionAdviceStatus'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useState } from 'react'

import type { ChoiceContextView, EvidenceView } from '../api/http'
import type { choiceRadios } from '../choiceGroup'
import { evidenceRoleLabel, observationText, materialReasonLabel } from '../labels'
import { useReviewContext } from '../router'

import type { ReviewField } from '../fieldDecision'
export function ChoiceEvidencePanel({
  context,
  evidenceId,
  label,
}: {
  context: ChoiceContextView | undefined
  evidenceId: string
  label: string
}) {
  const corroboratingSources = new Set(
    context?.evidence
      .filter((row) => row.role === 'external_corroboration')
      .map((row) => row.source),
  ).size
  return (
    <section className="choice-evidence" id={evidenceId} aria-label={`${label} evidence`}>
      {corroboratingSources ? (
        <p>
          {corroboratingSources}{' '}
          {corroboratingSources === 1 ? 'source corroborates' : 'sources corroborate'} this choice.
        </p>
      ) : null}
      {context?.evidence.length ? (
        <ul>
          {context.evidence.map((row, index) => (
            <li key={`${row.source}:${row.observation}:${String(index)}`}>
              <strong>{row.source}</strong>
              <span>{evidenceRoleLabel(row.role)}</span>
              <p>{observationText(row.observation)}</p>
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
            <p key={limitation}>
              {materialReasonLabel(limitation) ??
                // A limitation is a comparison condition or the server's own
                // sentence; an unnamed condition code is never shown.
                (/^[a-z0-9_]+$/u.test(limitation)
                  ? 'Another comparison condition applies'
                  : limitation)}
            </p>
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
  // The section is there only when this field has observations to show, more
  // may be loaded, or they could not be read (#1519).
  if (!observations.length && !evidence.isError && !evidence.hasNextPage && !missing) return null
  return (
    <section className="recorded-field-observations" aria-label="Recorded observations">
      <h3>Recorded observations</h3>
      {observations.length ? (
        <ul className="evidence-values">
          {observations.map((item, index) => (
            <li key={`${item.source}:${item.observation}:${String(index)}`}>
              <p>{observationText(item.observation)}</p>
              <small>
                {item.source} · {evidenceRoleLabel(item.role)}
              </small>
            </li>
          ))}
        </ul>
      ) : null}
      {missing ? <p>Some referenced observations are unavailable.</p> : null}
      {evidence.isError ? (
        <p role="alert">
          The observations could not be loaded.{' '}
          <button
            type="button"
            onClick={() =>
              void (evidence.isFetchNextPageError ? evidence.fetchNextPage() : evidence.refetch())
            }
          >
            Retry observations
          </button>
        </p>
      ) : null}
      {evidence.hasNextPage ? (
        <p>
          More observations remain.{' '}
          <button
            type="button"
            disabled={evidence.isFetchingNextPage}
            onClick={() => void evidence.fetchNextPage()}
          >
            {evidence.isFetchingNextPage ? 'Loading observations…' : 'Load more observations'}
          </button>
        </p>
      ) : null}
    </section>
  )
}
export function MetadataChoice({
  advice,
  retainedAdvice = false,
  context,
  current,
  disabled,
  evidenceName,
  expanded,
  label,
  longDescription,
  onChoose,
  radio,
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
  // The Evidence control's name when the card has no visible label (#1519).
  evidenceName?: string
  expanded: boolean
  label: string
  longDescription: boolean
  onChoose: () => void
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
    evidenceName ??
    (longDescription ? `Evidence for ${label} description` : `Evidence for ${label}: ${value}`)
  const rationale = adviceSource
    ? `${adviceSource.engine} · ${adviceSource.model}`
    : context?.rationale
  const recommendation = recording
    ? 'Recording…'
    : advice
      ? descriptionAdviceStatus(advice, retainedAdvice)
      : context?.recommendation === 'recommended'
        ? context.recommendationReason
        : ''
  const hasEvidence = Boolean(context && (context.evidence.length || context.limitations.length))
  return (
    <article
      className={`metadata-choice${current ? ' is-current' : ''}${context?.recommendation === 'recommended' ? ' is-recommended' : ''}${selected ? ' is-selected' : ''}${expanded ? ' is-evidence-open' : ''}`}
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
        aria-label={`Choose ${advice ? 'advised' : current ? 'current' : 'proposed'} ${longDescription ? 'description' : `value: ${value}`}`}
        {...radio}
        disabled={disabled}
        onClick={onChoose}
      >
        <small>
          {label}
          {context?.recommendation === 'recommended' ? `${label ? ' · ' : ''}Recommended` : ''}
          {selected ? <b className="choice-chosen">Your choice</b> : null}
        </small>
        <strong>
          {longDescription
            ? `Choose ${advice ? 'advised' : current ? 'current' : 'proposed'} description`
            : value}
        </strong>
        {rationale ? <span>{rationale}</span> : null}
        {recommendation ? <span className="choice-recommendation">{recommendation}</span> : null}
      </button>
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
        <ChoiceEvidencePanel context={context} evidenceId={evidenceId} label={label} />
      ) : null}
    </article>
  )
}
