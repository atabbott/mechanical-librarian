import type { ReviewerAccessView } from '../api/http'

export const ReviewerCost = ({
  view,
  model,
  task,
}: {
  view: ReviewerAccessView
  model: string
  task: 'cover' | 'description'
}) => {
  const estimate = view.cost_evidence.models.find((row) => row.model === model && row.task === task)
  return (
    <div>
      <dt>Measured request cost</dt>
      <dd>
        {estimate
          ? `About $${Number(estimate.per_1000_usd).toLocaleString('en-US', { maximumSignificantDigits: 4 })} per 1,000 ${task} review requests (estimate).`
          : 'Not measured for this model.'}
      </dd>
    </div>
  )
}
