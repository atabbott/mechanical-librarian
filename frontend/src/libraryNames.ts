import type { ReviewHttp } from './api/http'
import { queryWorkClass, useRequiredQuery as useQuery } from './queryWork'

/**
 * Name a library by its Settings display name. The Settings read is shared by
 * every route and issued only once `enabled`; while it is unavailable the slug
 * stands. `ready` is false while an enabled read is still pending, so a live
 * region can wait and announce one name rather than the slug and then the name.
 */
export const useLibraryName = (http: ReviewHttp, enabled: boolean) => {
  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: (queryContext) => http.getSettings(queryContext.signal, queryWorkClass(queryContext)),
    retry: false,
    enabled,
  })
  return {
    ready: !(enabled && settings.isPending),
    name: (name: string) =>
      settings.data?.document.libraries.find((library) => library.name === name)?.display_name ??
      name,
  }
}
