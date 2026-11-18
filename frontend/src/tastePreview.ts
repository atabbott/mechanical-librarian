import { queryOptions } from '@tanstack/react-query'

import type { ReviewHttp, TastePreviewView, TasteProtection } from './api/http'
import { queryWorkClass } from './queryWork'

// The taste preview's read and its one line (#1372 PR 5, K7).

const previewLimit = 100
const pollMilliseconds = 5_000

export const tastePreviewQuery = (http: ReviewHttp, protection: TasteProtection) =>
  queryOptions({
    queryKey: ['covers', 'taste', 'preview', protection] as const,
    queryFn: (context) => http.getTastePreview(protection, context.signal, queryWorkClass(context)),
    // Read again every five seconds while the suggestion model is checking,
    // live updates or not: an idle page defers live updates, so they alone
    // could leave the preview at "so far". It stops once it is done.
    refetchInterval: (query) => (query.state.data?.state === 'checking' ? pollMilliseconds : false),
    retry: false,
  })

const covers = (count: number) => (count === 1 ? '1 cover' : `${String(count)} covers`)
/** The preview's one line: what it found in how many covers, and why it stopped. */
export const previewMeta = (view: TastePreviewView): string => {
  const found = view.found.length
  if (view.state === 'checking' && view.checked === 0) return 'Checking covers…'
  const lead =
    found === 0
      ? `Found no switches in ${covers(view.checked)}`
      : `Found ${found === 1 ? '1 switch' : `${String(found)} switches`} in ${covers(view.checked)}`
  const rerun = view.rerun ? ', starting with last time’s.' : '.'
  if (view.state === 'checking') return `${lead} so far${rerun}`
  if (view.checked >= previewLimit) return `${lead}, the limit.`
  return `${lead}${rerun}`
}
