import type { DescriptionAdviceView } from './api/http'

export const descriptionAdviceStatus = (advice: DescriptionAdviceView, selected = false) => {
  if (selected) {
    if (advice.application_outcome === 'verified') return 'Applied and verified in the library.'
    if (advice.application_outcome === 'no_change_required')
      return 'No change needed: the library already has this description.'
    return advice.selected_advice?.current
      ? 'Activity shows whether the library was changed.'
      : 'Out of date. History still lists it.'
  }
  if (advice.state === 'requested' || advice.state === 'running')
    return 'Preparing description advice…'
  if (advice.failure === 'unavailable')
    return 'The description adviser is unavailable. Check it in Settings → Reviewer, then retry.'
  if (advice.failure === 'invalid_output')
    return 'The adviser’s answer could not be read. Retry later.'
  if (advice.state === 'interrupted') return 'Preparing advice was interrupted. Retry to ask again.'
  if (!advice.current)
    return 'This advice is out of date. Choose another value, or retry to ask for new advice.'
  return advice.candidate?.output.disposition === 'needs_review'
    ? 'Needs you. This description is never applied automatically.'
    : 'Ready to choose.'
}
