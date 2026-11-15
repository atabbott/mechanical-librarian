import type { ReviewHttp } from './api/http'
import { queryWorkClass, useRequiredQuery as useQuery } from './queryWork'

// How a Book matching check that gave up is retried (#1355): a save in store
// mode, a restart in file mode, where the setting cannot be saved. The answer
// waits for the settings read, the same one Policy's Book matching section
// makes, so file mode never reads the store mode's sentence first. A read that
// fails names no way to retry rather than guess one.
export const givenUpCopy = 'Could not be checked against the new Book matching setting.'
export const useBookMatchingRetry = (
  http: ReviewHttp,
): { answered: false } | { answered: true; retry: string | null } => {
  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: (queryContext) => http.getSettings(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
  })
  if (settings.isError) return { answered: true, retry: null }
  if (!settings.isSuccess) return { answered: false }
  return {
    answered: true,
    retry:
      settings.data.authority === 'file'
        ? 'Restart the Mechanical Librarian service to retry.'
        : 'Save Book matching again to retry.',
  }
}
