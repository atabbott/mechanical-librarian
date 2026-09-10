import type { DescriptionAdviceView } from './api/http'

export const descriptionAdviceStatus = (advice: DescriptionAdviceView) =>
  advice.application_outcome === 'verified'
    ? 'Applied and verified in the library. This authored result is retained in History.'
    : advice.application_outcome === 'no_change_required'
      ? 'No change needed in the library. This authored choice is retained in History.'
      : !advice.current
        ? 'Stale advice. Retained in history; select a current or provider value, or request new advice.'
        : advice.selection
          ? 'Selected. Activity shows whether the guarded write completed or no change was needed.'
          : advice.state === 'requested' || advice.state === 'running'
            ? 'Preparing description advice…'
            : advice.candidate?.output.disposition === 'needs_review'
              ? 'Needs review. This authored option cannot apply automatically.'
              : advice.candidate
                ? 'Ready for review. Authored advice is not provider evidence.'
                : advice.state === 'interrupted'
                  ? 'Preparation was interrupted. Retry only when you want another attempt.'
                  : 'Description advice could not be prepared. No automatic retry is implied.'
