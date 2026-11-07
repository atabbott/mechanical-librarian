/**
 * Hands focus to what replaced a retry's control once the retry has settled:
 * the content it loaded, a new Try again, or Reload when the tab is on an
 * earlier build. Call it when the read settles, with the target chosen from
 * the result. The move waits two timer turns, so that React has rendered the
 * branch that replaced the control: in Chromium one turn can be too early (a
 * more-transitions Try again found Reload not yet rendered). It applies only
 * while focus is lost (on the page or a disabled control) or still inside
 * `scope`, the control or region the retry held focus on. Focus the owner put
 * anywhere else stays there. The target is scrolled into view only as far as
 * it takes; the page's scroll padding keeps it clear of the masthead.
 */
export const focusAfterRetry = (
  target: () => HTMLElement | null | undefined,
  scope?: () => HTMLElement | null | undefined,
) => {
  window.setTimeout(() =>
    window.setTimeout(() => {
      const active = document.activeElement
      const lost = active === null || active === document.body || active.matches(':disabled')
      if (!lost && !scope?.()?.contains(active)) return
      const element = target()
      if (!element) return
      element.focus({ preventScroll: true })
      // The target can sit above the masthead or below the fold (a Reload at
      // the top of a long panel, or the foot of the rail's drawer): scroll
      // only as far as it takes to show it and its focus ring.
      element.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }),
  )
}

/**
 * Runs a retry whose read replaces the control that started it. A query that
 * failed on its first read has no data, so its refetch goes back to pending
 * and renders the loading branch in place of Try again. Focus moves first to
 * `region`, which stays mounted through the read, so it never falls to the
 * page; when the read settles it moves on to the target chosen from the
 * result, with `region` as the scope.
 */
export const retryInPlace = <Result>(
  region: () => HTMLElement | null | undefined,
  read: () => Promise<Result>,
  target: (result: Result) => HTMLElement | null | undefined,
) => {
  region()?.focus({ preventScroll: true })
  void read().then((result) => {
    focusAfterRetry(() => target(result), region)
  })
}
