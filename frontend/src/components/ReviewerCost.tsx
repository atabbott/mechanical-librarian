import type { ReviewerAccessView } from '../api/http'

type Row = ReviewerAccessView['cost_evidence']['models'][number]

const perThousandBooks = (row: Row) => {
  const value = (Number(row.total_cost_usd) / row.works) * 1000
  return value < 0.01 ? 'under $0.01' : `about $${value.toFixed(2)}`
}

// Example spend for 1,000 books from the packaged measurement of this exact
// model. Run dates and sample counts stay in the evidence file, not the app. Each task is stated on its own, and an unmeasured one says so rather
// than borrowing another model's or task's figure.
export const ReviewerCost = ({
  view,
  model,
  task,
}: {
  view: ReviewerAccessView
  model: string
  task: 'cover' | 'description'
}) => {
  const kinds =
    task === 'cover' ? (['cover', 'description'] as const) : (['description', 'cover'] as const)
  const rows = kinds.map((kind) => ({
    kind,
    row: view.cost_evidence.models.find((item) => item.model === model && item.task === kind),
  }))
  return (
    <div>
      <dt>Example cost per 1,000 books</dt>
      {rows.map(({ kind, row }) => (
        <dd key={kind}>
          {kind === 'cover' ? 'Cover review' : 'Description advice'}:{' '}
          {row
            ? `${perThousandBooks(row)}${kind === 'cover' ? `, including about ${(row.distinct_images / row.works).toFixed(1)} cover images per book` : ''}.`
            : 'not measured for this model.'}
        </dd>
      ))}
      {rows.some(({ row }) => row) ? (
        <dd>
          Estimated from a measured run. Your library and current prices will differ; this is an
          example, not a quote.
        </dd>
      ) : null}
    </div>
  )
}
