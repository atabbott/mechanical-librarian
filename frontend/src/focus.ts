import { useEffect } from 'react'

export const useRouteReadyFocus = (identity: string, preserveFocusedSearch = false) => {
  useEffect(() => {
    // Whoever holds focus at the moment the route becomes ready. The move is
    // deferred a frame so the new content is laid out first, and in that gap a
    // pointer or keyboard interaction can land on a control inside it. Taking
    // focus back then loses the interaction: the key that follows reaches this
    // container instead of the control the reader chose. So the move only
    // applies while focus is still where the route left it.
    const path = window.location.pathname
    const main = document.querySelector<HTMLElement>('#main-content')
    const focusIdentity = `${path}:${identity}`
    if (main && main.dataset.routeFocusIdentity !== focusIdentity)
      delete main.dataset.routeFocusIdentity
    const held = document.activeElement
    const frame = requestAnimationFrame(() => {
      if (main?.dataset.routeFocusIdentity === focusIdentity) return
      const target = document.querySelector<HTMLElement>('#main-content [data-route-focus]')
      if (document.activeElement !== held && document.activeElement !== target) return
      // Library's debounced search updates its URL while the owner is typing.
      // Keep that input's caret; pagination and route controls still move focus.
      if (preserveFocusedSearch && held?.matches('input[type="search"]') && main?.contains(held))
        return
      if (!target) return
      if (document.activeElement !== target) target.focus({ preventScroll: true })
      if (main) main.dataset.routeFocusIdentity = focusIdentity
    })
    return () => cancelAnimationFrame(frame)
  }, [identity, preserveFocusedSearch])
}
