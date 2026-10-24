import type { ReviewHttp } from './api/http'
import { queryWorkClass, useRequiredQuery as useQuery } from './queryWork'

/**
 * Name a library by its Settings display name. The Settings read is shared by
 * every route and issued only once `enabled`; while it is pending or
 * unavailable the slug stands.
 */
export const useLibraryName = (http: ReviewHttp, enabled: boolean) => {
  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: (queryContext) => http.getSettings(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
    enabled,
  })
  return (name: string) =>
    settings.data?.document.libraries.find((library) => library.name === name)?.display_name ?? name
}
