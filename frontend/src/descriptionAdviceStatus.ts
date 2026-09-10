import type { DescriptionAdviceView } from './api/http'

export const descriptionAdviceStatus = (advice: DescriptionAdviceView, selected = false) => {
  if (selected) {
    if (advice.application_outcome === 'verified')
      return 'Applied and verified in the library. This authored result is retained in History.'
    if (advice.application_outcome === 'no_change_required')
      return 'No change needed in the library. This authored choice is retained in History.'
    return advice.selected_advice?.current
      ? 'Selected. Activity shows whether the guarded write completed or no change was needed.'
      : 'Earlier choice retained in History. This choice is no longer current.'
  }
  if (advice.state === 'requested' || advice.state === 'running')
    return 'Preparing description advice…'
  if (advice.failure === 'unavailable')
    return 'Description adviser unavailable. Check Reviewer settings, then retry when ready.'
  if (advice.failure === 'invalid_output')
    return 'The adviser returned an invalid response. No new description was produced.'
  if (advice.state === 'interrupted')
    return 'Preparation was interrupted. Retry only when you want another attempt.'
  if (!advice.current)
    return 'Stale advice. Retained in history; select a current or provider value, or request new advice.'
  return advice.candidate?.output.disposition === 'needs_review'
    ? 'Needs review. This authored option cannot apply automatically.'
    : 'Ready for review. Authored advice is not provider evidence.'
}
