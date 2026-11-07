/**
 * Hands focus to what replaced a retry's control once the retry has settled:
 * the content it loaded, a new Try again, or Reload when the tab is on an
 * earlier build. Call it when the read settles, with the target chosen from
 * the result. The move waits two timer turns: the first lets the query
 * publish its result (React Query notifies on a timer, after a mutation's own
 * callbacks have run), the second lets React render the branch that replaced
 * the control. It applies only while focus is lost (on the page or a disabled
 * control) or still inside `scope`, the control or region the retry held
 * focus on. Focus the owner put anywhere else stays there.
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
      target()?.focus({ preventScroll: true })
    }),
  )
}
