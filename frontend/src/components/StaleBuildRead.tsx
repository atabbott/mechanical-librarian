import type { Ref } from 'react'

import { reloadCurrentApplication } from '../chunkRecovery'

/**
 * A read refused because the server runs a newer build cannot succeed on
 * another try: only a reload brings the tab onto that build. The shell's build
 * notice is the alert, so this names what was not loaded and offers the reload
 * without announcing again. `ref` reaches the reload button, for a site that
 * moves focus to its recovery control after a failed retry.
 */
export const StaleBuildRead = ({ lead, ref }: { lead: string; ref?: Ref<HTMLButtonElement> }) => (
  <p className="stale-build-read">
    {lead}{' '}
    <button ref={ref} type="button" onClick={() => reloadCurrentApplication()}>
      Reload
    </button>
  </p>
)
