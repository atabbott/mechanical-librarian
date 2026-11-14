import { Link } from '@tanstack/react-router'

import type { ReviewHttp } from '../api/http'
import { useBookMatchingRetry } from '../bookMatchingRetry'

// A work whose fields wait for their check against a changed Book matching
// setting: its choices are disabled, and the case reads again by itself when
// the check finishes (#1355). A check that gave up, as Policy counts it, says
// how to retry: save Book matching again, or restart in file mode, where the
// setting cannot be saved.
export const caseCheckingCopy = 'Checking this work against the new Book matching setting.'

export function CaseCheckingLine({
  checking,
  givenUp,
  http,
}: {
  checking: boolean
  givenUp: boolean
  http: ReviewHttp
}) {
  if (!checking) return null
  return givenUp ? (
    <CaseCheckGivenUpLine http={http} />
  ) : (
    <p className="case-checking-line" role="status">
      {caseCheckingCopy}
    </p>
  )
}

function CaseCheckGivenUpLine({ http }: { http: ReviewHttp }) {
  // Said once the settings read has answered, so file mode never shows the
  // store mode's way to retry first.
  const retry = useBookMatchingRetry(http)
  if (!retry.answered) return null
  return (
    <p className="case-checking-line" role="status">
      This work could not be checked against the new{' '}
      <Link to="/policy" search={{ section: 'curation' }} hash="book-matching">
        Book matching
      </Link>{' '}
      setting.{retry.retry ? ` ${retry.retry}` : ''}
    </p>
  )
}
